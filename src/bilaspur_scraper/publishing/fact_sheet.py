"""Fact extraction from a transcript (the first LLM pass).

A transcript is prose. The site template needs a **structured sheet**:
a headline, a one-sentence lede, key points, stat tiles, sections and a
source. Extracting that sheet first - and checking it against the
transcript - is what keeps invented numbers and names out of a
published article.

The sheet is deliberately *not* prose. Every field is a short factual
string that either appears in the transcript or is a label the writer
chooses ("क्या है मामला:"), so it can be checked mechanically.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"

#: The hosted model. `minimax-m3.1-flash` is used rather than the
#: deepseek reasoning model because the reasoning model spends minutes
#: of its budget thinking before emitting JSON - measured at a 90 s
#: timeout on the fact-extraction shape - while this model answers the
#: same request in about 11 s with clean JSON.
GENERATION_MODEL = "minimaxai/minimax-m3.1-flash"

#: Tried in turn when the first model fails. The hosted endpoint is
#: flaky - the same request intermittently answers HTTP 503 - so a
#: second model is a cheap way to get an answer. These were verified as
#: callable on this account (2026-11-16).
FALLBACK_MODELS = (
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "mistralai/mistral-large-4-1t-a52b",
    "nvidia/nemotron-3-ultra-550b-a55b",
)

#: A reasoning model spends the budget thinking; a small max_tokens
#: returns content=None with finish_reason="length".
GEN_MAX_TOKENS = 3000

#: A transcript longer than this is trimmed. A live stream can run for
#: hours; a news brief covers one story, and the model is far more
#: reliable on a bounded input.
MAX_TRANSCRIPT_CHARS = 8000

#: Topics that `SKILLS.md` §10 requires to go to human review before
#: editorial use.
SENSITIVE_KEYWORDS = (
    "crime", "accident", "flood", "fire", "rape", "murder", "kill", "attack",
    "stampede", "disease", "health", "hospital", "election", "vote", "poll",
    "protest", "strike", "arrest", " FIR", "अपराध", "हत्या", "आग", "बवाल",
    "दुर्घटना", "बाढ़", "चुनाव", "वोट", "हड़ताल", "प्रदर्शन", "गिरफ्तार",
    "स्वास्थ्य", "अस्पताल", "मारपीट", "लूट", "धोखाधड़ी",
)

EXTRACTION_PROMPT = (
    "You extract structured facts from a Hindi news transcript for a "
    "Bilaspur (Chhattisgarh) local news site.\n"
    "\n"
    "Reply with a JSON object that has EXACTLY these keys, spelled exactly "
    "as written, in English. Do not translate the key names and do not "
    "add any other key:\n"
    "{\n"
    '  "headline": string,        // one factual Hindi headline\n'
    '  "lede": string,            // ONE sentence, the core fact\n'
    '  "keypoints": [             // 2-3 items, each:\n'
    '     {"label": string,       //   short bold label, e.g. "क्या है मामला:"\n'
    '      "detail": string}      //   one factual sentence, no trailing colon\n'
    '  ],\n'
    '  "stats": [                 // 0-3 items, each:\n'
    '     {"value": string,       //   a figure or date, e.g. "1000 मीटर"\n'
    '      "label": string}       //   what the figure means, one short line\n'
    '  ],\n'
    '  "sections": [              // 2-4 items, each:\n'
    '     {"heading": string,      //   a short sub-heading\n'
    '      "paragraphs": [string]}//   one or two Hindi paragraphs\n'
    '  ],\n'
    '  "source": string           // where the claim came from\n'
    "}\n"
    "\n"
    "The VALUES are in Hindi. The KEYS are in English, exactly as above.\n"
    "\n"
    "RULES:\n"
    "- Use ONLY information present in the transcript. Never add a fact, a "
    "name, a figure, a date or a place that is not in it.\n"
    "- Keep the original Hindi wording for names, places and numbers.\n"
    "- Attribute claims: if the transcript says 'पुलिस ने बताया', keep that.\n"
    "- Do not speculate about guilt, motive or identity.\n"
    "- If the transcript does not say something, leave that field out "
    "rather than guessing.\n"
    "- Reply with the JSON object only. No commentary, no markdown fence."
)


class KeypointModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    label: str = Field(description="A short bold label, e.g. 'क्या है मामला:'")
    detail: str = Field(description="One factual sentence, no trailing colon")


class StatModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: str = Field(description="A figure or date, e.g. '1000 मीटर'")
    label: str = Field(description="What the figure means, one short line")


class SectionModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    heading: str = Field(description="A short sub-heading, e.g. 'क्या है मामला'")
    paragraphs: list[str] = Field(
        description="One or two factual paragraphs in Hindi")


class FactSheetModel(BaseModel):
    """The LLM's structured output, before it becomes a `FactSheet`."""
    model_config = ConfigDict(frozen=True)

    headline: str = Field(
        description="A factual Hindi headline. No opinion, no clickbait.")
    lede: str = Field(
        description="One sentence summarising the core fact. 20-40 words.")
    keypoints: list[KeypointModel] = Field(
        default_factory=list, max_length=4,
        description="2-3 key points, each a label plus a detail")
    stats: list[StatModel] = Field(
        default_factory=list, max_length=3,
        description="0-3 figures worth showing as tiles")
    sections: list[SectionModel] = Field(
        default_factory=list, max_length=5,
        description="2-4 sections, each with a heading and paragraphs")
    source: str = Field(
        default="स्थानीय मीडिया रिपोर्ट",
        description="Where the claim came from, e.g. 'बिलासपुर ज़िला प्रशासन'")


def _json_from(text: str) -> dict | None:
    """Pull a JSON object out of a model reply.

    The model may wrap the JSON in prose or a code fence, and it may
    return the sheet nested under a key ("result", "data", "article"),
    so the object is located by its braces and unwrapped if needed.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    # unwrap a single nesting level if the sheet is under one key
    if "headline" not in parsed:
        for value in parsed.values():
            if isinstance(value, dict) and "headline" in value:
                return value
    return parsed


def is_sensitive(text: str) -> bool:
    """Does this story need human review before publication?

    `SKILLS.md` §10: crime, accidents, health, legal matters, elections,
    communal tension and disasters are marked `needs_review` and are
    never published automatically.
    """
    lowered = text.lower()
    return any(k.lower() in lowered for k in SENSITIVE_KEYWORDS)


def extract_facts(transcript: str, *, channel: str | None = None,
                  attempts: int = 8) -> Optional[FactSheetModel]:
    """Turn a transcript into a structured fact sheet.

    The hosted endpoint is intermittently unavailable: the same request
    succeeds and then fails with HTTP 503 ("content safety screening is
    temporarily unavailable"), which is a flaky upstream service rather
    than a content problem - the identical payload was measured
    succeeding and failing across consecutive calls. So the call is
    retried generously with a jittered pause.
    """
    import time

    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    client = OpenAI(base_url=NVIDIA_BASE, api_key=key, timeout=180.0,
                    max_retries=0)

    prompt = f"Channel: {channel or 'unknown'}\n\nTranscript:\n{transcript}"
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
        prompt = f"Channel: {channel or 'unknown'}\n\nTranscript:\n{transcript}"

    last_error = ""
    models = (GENERATION_MODEL,) + FALLBACK_MODELS
    attempt = 0
    # The hosted endpoint answers 503 for roughly a third of calls, and
    # the failures are not correlated with the content. So the retry
    # rotates through the models: when one is overloaded the next is
    # tried immediately rather than after a long pause.
    for model in models:
        for _ in range(3):
            attempt += 1
            try:
                r = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": EXTRACTION_PROMPT},
                              {"role": "user", "content": prompt}],
                    max_tokens=GEN_MAX_TOKENS, temperature=0.2)
                text = (r.choices[0].message.content or "").strip()
                data = _json_from(text)
                if data:
                    try:
                        return FactSheetModel.model_validate(data)
                    except Exception as exc:  # noqa: BLE001
                        last_error = f"schema mismatch: {str(exc)[:200]}"
                else:
                    last_error = ("no JSON in reply "
                                  f"(finish_reason={r.choices[0].finish_reason})")
                    break
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {str(exc)[:140]}"
                logger.info("fact extraction attempt %d (%s) failed: %s",
                            attempt, model, last_error)
                if "429" in last_error or "RateLimit" in last_error:
                    time.sleep(min(12 * attempt, 40) + _jitter(attempt))
                elif ("503" in last_error or "Service Unavailable" in last_error
                      or "InternalServerError" in last_error):
                    # overloaded right now - a short pause, then the next
                    # model is tried
                    time.sleep(min(3 * attempt, 10) + _jitter(attempt))
                else:
                    break
                continue
            break
    logger.warning("fact extraction failed after %d attempts: %s",
                   attempt, last_error)
    return None


def _jitter(attempt: int) -> float:
    """A few seconds of jitter, so retries do not march in lockstep."""
    import random
    return random.uniform(1.0, 5.0 + attempt)


def to_fact_sheet(model: FactSheetModel, *, transcript: str,
                  channel: str | None = None,
                  video_url: str | None = None,
                  published_at: str | None = None) -> "FactSheet":
    """Convert the model output into the template's fact sheet.

    The sensitive flag is computed here from the transcript, not taken
    from the model - it is a governance decision, not a summary.
    """
    from .site_template import FactSheet, Keypoint, Section, Stat

    return FactSheet(
        headline=model.headline.strip(),
        lede=model.lede.strip(),
        keypoints=tuple(Keypoint(label=k.label.strip(), detail=k.detail.strip())
                        for k in model.keypoints if k.detail.strip()),
        stats=tuple(Stat(value=s.value.strip(), label=s.label.strip())
                    for s in model.stats if s.value.strip()),
        sections=tuple(Section(heading=s.heading.strip(),
                               paragraphs=tuple(p.strip() for p in s.paragraphs
                                                if p.strip()))
                       for s in model.sections if s.heading.strip()),
        source=model.source.strip() or "स्थानीय मीडिया रिपोर्ट",
        sensitive=is_sensitive(transcript),
        published_at=published_at,
        channel=channel,
        video_url=video_url,
    )
