"""The publishing pipeline: video -> published WordPress draft.

    transcript
        -> fact sheet (LLM pass 1, checked against the transcript)
        -> site HTML (the `lnb-` template)
        -> featured image (generated, 1200x675)
        -> WordPress draft (media upload + post create)

The post is created as a **draft**. Publishing is a separate, explicit
call - `SKILLS.md` §10 requires human corroboration for crime, accident,
health, legal and election material, and §16 lists posting to WordPress
as something that must not happen without separate authorisation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .fact_sheet import extract_facts, is_sensitive, to_fact_sheet
from .featured_image import alt_text_for, render_featured_image
from .site_template import (
    FactSheet,
    excerpt_text,
    render_post,
    slugify_headline,
)
from .wordpress import PostResult, WordPressClient, WordPressError

logger = logging.getLogger(__name__)


@dataclass
class PublishResult:
    """What happened for one video."""
    video_url: str
    ok: bool = False
    status: str = ""
    headline: str = ""
    slug: str = ""
    post_id: Optional[int] = None
    link: str = ""
    media_id: Optional[int] = None
    sensitive: bool = False
    fact_check: dict = field(default_factory=dict)
    html: str = ""
    error: str = ""


def check_facts(facts: FactSheet, transcript: str) -> dict:
    """Check the fact sheet against the transcript.

    The same mechanical check the article pipeline uses: every number and
    proper noun in the generated text must appear in the transcript it
    came from. Labels (`क्या है मामला:`) are exempt - they are the
    writer's own structure, not a claim.
    """
    numbers = set(re.findall(r"\d+", transcript.lower()))
    flags: list[str] = []

    text_parts = [facts.lede, facts.headline]
    text_parts += [k.detail for k in facts.keypoints]
    text_parts += [s.value + " " + s.label for s in facts.stats]
    text_parts += [sec.heading + " " + " ".join(sec.paragraphs)
                   for sec in facts.sections]
    blob = "\n".join(text_parts)

    for n in set(re.findall(r"\d+", blob.lower())):
        if n not in numbers:
            flags.append(f"number not in transcript: {n}")

    # proper nouns: Latin words that are capitalised in the sheet
    stop = {"bilaspur", "news", "video", "channel", "subscribe", "local"}
    lowered = transcript.lower()
    for word in set(re.findall(r"[A-Za-z][A-Za-z]{2,}", blob)):
        if word.lower() in stop or not word[0].isupper():
            continue
        if word.lower() not in lowered:
            flags.append(f"proper noun not in transcript: {word}")

    return {"ok": not flags, "flags": flags,
            "checked_chars": len(blob),
            "transcript_chars": len(transcript)}


#: The site's own category slugs, mapped from the words that select
#: them. The ids are read from the live site at runtime, so a new
#: category is picked up without a code change; this map only says
#: which slug a story belongs to.
#:
#: The words are deliberately narrow. "पुलिस" alone must not select
#: `crime` - a traffic story quotes the police too - so the crime rule
#: looks for the *act* (an assault, a theft, an arrest), not the
#: institution.
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bilaspur", ()),                       # always
    ("breaking", ("breaking", "अभी", "तत्काल")),
    ("crime", ("अपराध", "मारपीट", "लूट", "गिरफ्तार", "crime", "assault",
               "murder", "fraud", "धोखाधड़ी", "हत्या", "चोरी", "बलात्कार")),
    ("traffic", ("ट्रैफिक", "traffic", "accident", "दुर्घटना", "road",
                 "सड़क", "crash", "collison", "टक्कर")),
    ("health", ("स्वास्थ्य", "health", "hospital", "अस्पताल", "disease",
                "डेंगू", "covid", "medical", "वार्ड")),
    ("politics", ("राजनीति", "politics", "mla", "विधायक", "चुनाव",
                  "election", "minister", "मंत्री", "विधानसभा")),
    ("local-events", ("समारोह", "festival", "त्योहार", "मेला", "पूजा",
                      "चठ", "नगर सभा", "जश्न")),
    ("nagar-nigam", ("नगर निगम", "nagar nigam", "municipal", "निगम")),
    ("district-administration", ("जिला प्रशासन", "कलेक्टर", "collector",
                                 "district administration", "tehsil",
                                 "तहसील")),
    ("jobs-careers", ("भर्ती", "recruitment", "naukri", "रोजगार", "vacancy",
                      "jobs", "career", "walk-in")),
    ("education", ("शिक्षा", "education", "school", "विद्यालय", "college",
                   "महाविद्यालय", "university", "विश्वविद्यालय", "ugc",
                   "net exam")),
    ("business", ("व्यापार", "business", "market", "बाजार", "shop",
                  "दुकान", "factory", "कारखाना")),
    ("sports", ("खेल", "sports", "cricket", "क्रिकेट", "football",
                "टेनिस", "tournament", "टूर्नामेंट")),
    ("entertainment", ("मनोरंजन", "entertainment", "movie", "फिल्म",
                       "bollywood", "सिनेमा")),
)


def choose_categories(facts: FactSheet, *, available_slugs: dict[str, int],
                      default_slug: str = "bilaspur") -> list[int]:
    """Pick the site's categories for a story.

    `bilaspur` is always included. The rest come from the words in the
    story, matched against the site's own category list. Ids are looked
    up at runtime, so a slug the site does not have is skipped rather
    than sent as a wrong id.
    """
    ids: list[int] = []
    haystack = " ".join([
        facts.headline, facts.lede,
        " ".join(k.label + " " + k.detail for k in facts.keypoints),
        " ".join(s.value + " " + s.label for s in facts.stats),
        " ".join(sec.heading + " " + " ".join(sec.paragraphs)
                  for sec in facts.sections),
    ]).lower()

    for slug, words in CATEGORY_RULES:
        if slug not in available_slugs:
            continue
        if not words:
            ids.append(available_slugs[slug])
            continue
        if any(w.lower() in haystack for w in words):
            ids.append(available_slugs[slug])
    if not ids and default_slug in available_slugs:
        ids.append(available_slugs[default_slug])
    return ids


def build_post_payload(facts: FactSheet, *, channel: str | None = None,
                       include_ads: bool = True) -> dict:
    """The body HTML, the excerpt and the slug for a fact sheet."""
    html = render_post(facts, include_ads=include_ads)
    return {
        "title": facts.headline,
        "content": html,
        "excerpt": excerpt_text(facts),
        "slug": slugify_headline(facts.headline),
        "html": html,
    }


def publish_from_transcript(
    transcript: str, *,
    client: WordPressClient,
    video_url: str | None = None,
    channel: str | None = None,
    published_label: str | None = None,
    categories: list[int] | None = None,
    include_ads: bool = True,
    image_dir: Path | None = None,
    publish: bool = False,
) -> PublishResult:
    """Turn one transcript into a WordPress draft (and optionally publish
    it)."""
    result = PublishResult(video_url=video_url or "")

    model = extract_facts(transcript, channel=channel)
    if model is None:
        result.status = "no_facts"
        result.error = "could not extract a fact sheet from the transcript"
        return result

    facts = to_fact_sheet(model, transcript=transcript, channel=channel,
                          video_url=video_url, published_at=published_label)
    result.headline = facts.headline
    result.sensitive = facts.sensitive

    check = check_facts(facts, transcript)
    result.fact_check = check
    if not check["ok"]:
        result.status = "fact_check_failed"
        result.error = "; ".join(check["flags"])
        return result

    payload = build_post_payload(facts, channel=channel,
                                 include_ads=include_ads)
    result.html = payload["html"]
    result.slug = payload["slug"]

    # the featured image, generated from the facts
    try:
        out_dir = image_dir or Path("data/featured")
        out_dir.mkdir(parents=True, exist_ok=True)
        image_path = out_dir / f"{result.slug}.jpg"
        render_featured_image(
            headline=facts.headline, channel=channel,
            source=facts.source, published_label=published_label,
            output_path=image_path)
        alt = alt_text_for(facts.headline, facts.source)
        media_id = client.upload_media(image_path, title=facts.headline,
                                       alt_text=alt)
        result.media_id = media_id
    except (WordPressError, OSError) as exc:
        # a missing image must not stop the article - the theme falls back
        # to no thumbnail
        logger.warning("featured image failed, posting without one: %s", exc)
        media_id = None

    try:
        post = client.create_post(
            title=payload["title"], content=payload["content"],
            excerpt=payload["excerpt"], slug=payload["slug"],
            categories=categories or [], featured_media=media_id,
            status="draft")
    except WordPressError as exc:
        result.status = "post_failed"
        result.error = str(exc)
        return result

    result.post_id = post.post_id
    result.link = post.link
    result.status = post.status

    # only a non-sensitive story may be published without a human, and
    # only when the caller explicitly asked for it
    if publish and not facts.sensitive:
        try:
            post = client.promote(post.post_id)
            result.status = post.status
            result.link = post.link
        except WordPressError as exc:
            result.error = f"published failed: {exc}"
    elif publish and facts.sensitive:
        result.error = ("left as a draft: this story is sensitive and "
                        "needs human review")

    result.ok = True
    return result
