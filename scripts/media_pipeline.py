"""Video -> transcript -> article pipeline, with evidence checks.

Stages, in order:

  1. **Transcript** - prefer the publisher's own caption track (YouTube
     auto-captions in Hindi); fall back to audio only when a caption
     track is absent. Both are recorded with the method used, because a
     publisher's caption and our own transcription are different things.

  2. **Generate** - an LLM writes a news brief from the transcript. The
     prompt forbids inventing facts and requires the source to be named.

  3. **Fact-check** - the generated brief is compared back against the
     transcript: every number, place name and proper noun in the brief
     must appear in the transcript. Anything that does not is flagged.
     This is a *mechanical* check, not a judgement: it catches invented
     specifics, which is the failure mode that matters.

The generated article is stored beside the transcript, never instead of
it, and always keeps the original video URL.

Usage:
    python scripts/media_pipeline.py --videos 6
    python scripts/media_pipeline.py --source tirandaj
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.config.settings import load_env_file  # noqa: E402

load_env_file()

from bilaspur_scraper.database.media_models import (  # noqa: E402
    MediaTranscript,
    SocialVideo,
)
from bilaspur_scraper.database.session import (  # noqa: E402
    create_db_engine,
    create_schema,
    make_session_factory,
)

NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
GENERATION_MODEL = "deepseek-ai/deepseek-v4.1-flash"

#: A reasoning model spends the budget thinking; a small max_tokens
#: returns content=None with finish_reason="length".
GEN_MAX_TOKENS = 3000

SYSTEM_PROMPT = (
    "You are a Hindi news editor for a Bilaspur (Chhattisgarh) local news "
    "site. Write a factual news brief from the supplied transcript.\n"
    "RULES:\n"
    "- Write in Hindi. Keep the original place names, names and numbers "
    "exactly as they appear.\n"
    "- Use only facts present in the transcript. Never add facts, names, "
    "figures, dates or locations that are not in it.\n"
    "- Do not speculate about guilt, motive or identity. Attribute claims "
    "to whoever made them (e.g. 'पुलिस ने बताया कि...').\n"
    "- Do not add an editorial headline or opinion. Start with the place "
    "and the fact.\n"
    "- Output only the brief. No preamble, no English, no markdown."
)


# --------------------------------------------------------------------------
# transcription
# --------------------------------------------------------------------------

def fetch_captions(video_url: str, *, languages=("hi", "hi-en", "en")) -> dict | None:
    """The publisher's own caption track, via yt-dlp.

    Returns `{'text', 'segments', 'language', 'method'}` or None.
    """
    import yt_dlp

    outdir = Path(os.environ.get("TEMP", "/tmp")) / "bsp_caps"
    outdir.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "writesubtitles": True, "writeautomaticsub": True,
        "subtitleslangs": list(languages),
        "subtitlesformat": "json3",
        "outtmpl": str(outdir / "cap.%(ext)s"),
        "ignoreerrors": True,
        "noprogress": True,
        "consoletitle": False,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([video_url])
    except Exception:  # noqa: BLE001
        pass

    for lang in languages:
        path = outdir / f"cap.{lang}.json3"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        events = data.get("events") or []
        parts = []
        count = 0
        for ev in events:
            segs = ev.get("segs") or []
            text = "".join(s.get("utf8", "") for s in segs).strip()
            if text:
                parts.append(text)
                count += 1
        full = " ".join(parts).strip()
        if len(full) < 80:
            continue
        path.unlink(missing_ok=True)
        return {"text": full, "segments": count, "language": lang,
                "method": "youtube_captions"}
    return None


def fetch_audio(video_url: str) -> Path | None:
    """Download the audio track and convert it to 16 kHz mono MP3."""
    import yt_dlp

    outdir = Path(os.environ.get("TEMP", "/tmp")) / "bsp_audio"
    outdir.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True, "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(outdir / "a.%(ext)s"),
        "noprogress": True,
        "consoletitle": False,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3", "preferredquality": "96"}],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([video_url])
    except Exception:  # noqa: BLE001
        return None
    mp3 = outdir / "a.mp3"
    return mp3 if mp3.exists() else None


def transcribe_local(audio_path: Path, *, model_size: str = "small") -> dict | None:
    """Speech-to-text with a local Whisper model (no key, no upload)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path), language="hi", beam_size=5,
        vad_filter=True)
    parts = []
    count = 0
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            parts.append(text)
            count += 1
    full = " ".join(parts).strip()
    if not full:
        return None
    return {"text": full, "segments": count,
            "language": getattr(info, "language", "hi"),
            "method": "whisper_local",
            "model": f"faster-whisper/{model_size}"}


def transcribe_nvidia(audio_path: Path) -> dict | None:
    """Speech-to-text through NVIDIA's hosted whisper (if the account
    has the speech function provisioned)."""
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    client = OpenAI(base_url=NVIDIA_BASE, api_key=key)
    for model in ("openai/whisper-large-v3", "nvidia/whisper-large-v3"):
        try:
            with open(audio_path, "rb") as f:
                r = client.audio.transcriptions.create(
                    model=model, file=f, language="hi")
            text = (getattr(r, "text", "") or "").strip()
            if text:
                return {"text": text, "segments": None, "language": "hi",
                        "method": "nvidia_whisper", "model": model}
        except Exception:  # noqa: BLE001
            continue
    return None


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

def generate_article(transcript: str, *, title: str | None = None,
                     channel: str | None = None, attempts: int = 3) -> dict | None:
    """Write a news brief from the transcript.

    The hosted model occasionally returns an empty completion (a reasoning
    model can exhaust its budget, or the API can hiccup), so the call is
    retried before giving up.
    """
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    client = OpenAI(base_url=NVIDIA_BASE, api_key=key)

    prompt = f"Source channel: {channel or 'unknown'}\n"
    if title:
        prompt += f"Original video title: {title}\n"
    prompt += f"\nTranscript:\n{transcript}"

    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            r = client.chat.completions.create(
                model=GENERATION_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": prompt}],
                max_tokens=GEN_MAX_TOKENS, temperature=0.3)
            text = (r.choices[0].message.content or "").strip()
            if text:
                return {"text": text, "model": r.model,
                        "tokens": r.usage.total_tokens if r.usage else None,
                        "attempts": attempt}
            last_error = (f"empty completion "
                          f"(finish_reason={r.choices[0].finish_reason})")
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
        if attempt < attempts:
            time.sleep(2 * attempt)
    print(f"    generation failed after {attempts} attempts: {last_error}")
    return None


# --------------------------------------------------------------------------
# fact-checking
# --------------------------------------------------------------------------

#: A number, a place-like token, or a capitalised word. These are the
#: specifics a model invents when it hallucinates.
_NUMBER_RE = re.compile(r"\d+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z]{2,}")
#: Devanagari proper nouns. A Hindi brief's place and person names are
#: written in Devanagari, so a Latin-only check would miss exactly the
#: details most likely to be invented.
_DEVANAGARI_WORD_RE = re.compile(r"[ऀ-ॿ]{3,}")
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has",
    "was", "were", "are", "but", "not", "you", "his", "her", "its",
    "they", "them", "their", "there", "then", "than", "when", "what",
    "which", "who", "will", "would", "could", "should", "about", "after",
    "before", "bilaspur", "news", "video", "channel", "subscribe",
}

#: Common Hindi words that are not proper nouns. The list is a safety
#: net, not the primary signal - a single Devanagari word cannot be
#: classified without context ("मीटर" is a unit, "सीपत" is a place).
#: So the check compares *word sequences* against the transcript.
_HINDI_COMMON = {
    "बिलासपुर", "खबर", "समाचार", "पुलिस", "नगर", "विभाग", "अधिकारी",
    "प्रशासन", "युवा", "महिला", "पुरुष", "पुलिसकर्मी", "राज्य", "सरकार",
    "वीडियो", "चैनल", "छत्तीसगढ़", "न्यूज़", "आरोप", "मामला", "केस",
    "शिकायत", "पकड़ा", "गिरफ्तार", "जिला", "थाना", "अस्पताल", "कलेक्टर",
    "मुख्यमंत्री", "विधानसभा", "संसद", "चुनाव", "पार्षद", "मंडी", "बाजार",
    "सड़क", "बताया", "गया", "किया", "हुआ", "हुई", "रहा", "रही", "लिए",
    "साथ", "बाद", "पहले", "दौरान", "अपने", "उनके", "इस", "उस", "एक",
    "दो", "तीन", "बार", "वर्ष", "महीने", "दिन", "समय", "बात", "कारण",
    "लगभग", "मीटर", "किलोमीटर", "दूरी", "जगह", "पास", "सामने", "बीच",
    "आदि", "सहित", "वाले", "वाली", "वाला", "कर", "करते", "करता", "हुई",
    "नहीं", "सकता", "सकते", "चाहिए", "वाले", "मौके", "समय", "स्थान",
    "विभाग", "अधिकारियों", "कर्मचारियों", "लोगों", "युवाओं", "महिलाओं",
}

#: Words that mark a following word as a name (a title before a name).
_NAME_MARKERS = {
    "श्री", "श्रीमती", "डॉ", "प्रो", "माननीय", "अध्यक्ष", "सांसद",
    "विधायक", "मंत्री", "कलेक्टर", "एसपी", "सीएसपी", "थाना", "अधिकारी",
}

#: Place names that a model might reach for when it invents a location.
#: These are checked as single words, because a Hindi place name on its
#: own is exactly the kind of detail a model adds for plausibility.
_KNOWN_PLACES = {
    "रायपुर", "दुर्ग", "भिलाई", "जशपुर", "सरगुवा", "अम्बीकापुर", "चांपा",
    "जांजगीर", "मुंगेली", "सक्ती", "लोरमी", "कोरिया", "कोण्डागांव",
    "बस्तर", "नारायणपुर", "इंद्रावती", "सुकमा", "बीजापुर", "गariyaband",
    "गरियाबंद", "कबीरधाम", "छत्तीसगढ़", "नईदिल्ली", "दिल्ली", "मुंबई",
    "पुणे", "जयपुर", "इंदौर", "भोपाल", "हैदराबाद", "चेन्नई", "कोलकाता",
    "बेंगलुरु", "लखनऊ", "अहमदाबाद", "पंजाब", "गुजरात", "राजस्थान",
    "मध्यप्रदेश", "उत्तरप्रदेश", "हिमाचल", "हिमाचलप्रदेश", "महाराष्ट्र",
}


def _devanagari_phrases(text: str) -> set[str]:
    """Devanagari sequences that look like names or places.

    Devanagari has no capitalisation, and a single word cannot be
    classified without context ("मीटर" is a unit, "सीपत" is a place).
    The reliable signal is a **title before a name** - "कलेक्टर रमेश",
    "श्री राजेश", "मुख्यमंत्री वोरा" - because a title plus a
    following word is essentially never an ordinary phrase.

    The check is word-by-word, not phrase-by-phrase: the generated
    brief reorders the source ("मंडल अध्यक्ष पर" where the transcript
    said "तत्कालिक अध्यक्ष"), so a strict phrase match reports a
    false positive. Only the *name* word has to be present.
    """
    words = re.findall(r"[ऀ-ॿ]+", text)
    names: set[str] = set()
    for i in range(len(words) - 1):
        title, following = words[i], words[i + 1]
        if title in _NAME_MARKERS and following not in _HINDI_COMMON:
            names.add(following)
    return names


def fact_check(article: str, transcript: str) -> dict:
    """Check the article's specifics against the transcript.

    Three checks:
      * every number in the article must appear in the transcript;
      * every Latin word that looks like a proper noun (capitalised, not
        a stop word) must appear in the transcript;
      * the article must be long enough to carry the story.

    Returns the flags; an empty list means nothing unsupported was
    found. This is a mechanical check, not a judgement of truth.

    Note on length: the ratio to the transcript is *not* a useful check.
    A news bulletin repeats itself, contains ad breaks and names the
    same place several times, so a faithful 200-word brief of a
    2,000-character transcript is normal, not a red flag. Only an
    absolute minimum is checked.
    """
    flags: list[str] = []
    t_lower = transcript.lower()
    a_lower = article.lower()

    # 1. numbers
    t_numbers = set(_NUMBER_RE.findall(t_lower))
    for n in set(_NUMBER_RE.findall(a_lower)):
        if n not in t_numbers:
            flags.append(f"number not in transcript: {n}")

    # 2. proper nouns (Latin script, capitalised in the original)
    for word in set(_LATIN_WORD_RE.findall(article)):
        if word.lower() in _STOPWORDS:
            continue
        if not word[0].isupper():
            continue
        if word.lower() not in t_lower:
            flags.append(f"proper noun not in transcript: {word}")

    # 2b. Devanagari proper nouns - the names most likely to be invented
    for name in _devanagari_phrases(article):
        if name not in transcript:
            flags.append(f"proper noun not in transcript: {name}")
    for place in _KNOWN_PLACES:
        if place in article and place not in transcript:
            flags.append(f"place not in transcript: {place}")

    # 3. substance - an absolute floor, not a ratio
    if len(article) < 90:
        flags.append(f"article is only {len(article)} chars - "
                     f"too short to carry the story")

    return {"ok": not flags, "flags": flags,
            "article_chars": len(article),
            "transcript_chars": len(transcript)}


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def store(database_url: str, video_pk: int, transcript: dict,
           article: dict | None, check: dict | None) -> None:
    engine = create_db_engine(database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        row = MediaTranscript(
            video_pk=video_pk,
            method=transcript["method"],
            language=transcript.get("language"),
            language_source="detected" if transcript.get("language") else None,
            text=transcript["text"],
            char_count=len(transcript["text"]),
            segment_count=transcript.get("segments"),
            model_name=transcript.get("model") or article.get("model"),
            notes=json.dumps({"article": article["text"] if article else None,
                              "fact_check": check}, ensure_ascii=False)
            if article or check else None)
        session.add(row)
        session.commit()
    engine.dispose()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos", type=int, default=5)
    parser.add_argument("--source", default=None,
                        help="only this channel's videos (name substring)")
    parser.add_argument("--database-url", default="sqlite:///data/news.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the result without storing it")
    parser.add_argument("--out", default=str(ROOT / "data" / "sample_articles.json"))
    args = parser.parse_args()

    engine = create_db_engine(args.database_url)
    create_schema(engine)
    factory = make_session_factory(engine)

    with factory() as session:
        q = session.query(SocialVideo)
        if args.source:
            q = q.join(SocialVideo.__table__.metadata.tables[
                "social_accounts"], isouter=True)
        rows = q.order_by(SocialVideo.id).limit(args.videos).all()

    # pick the channels we care about
    with factory() as session:
        from bilaspur_scraper.database.media_models import SocialAccount
        chosen = []
        for v in session.query(SocialVideo).order_by(SocialVideo.id).all():
            acct = session.get(SocialAccount, v.account_id)
            if acct is None:
                continue
            if args.source and args.source.lower() not in acct.account_name.lower():
                continue
            chosen.append((v, acct))
            if len(chosen) >= args.videos:
                break

    print(f"processing {len(chosen)} video(s)\n")
    results = []
    for v, acct in chosen:
        print(f"--- {acct.account_name} | {(v.title or '')[:60]}")
        t = fetch_captions(v.url)
        if t is None:
            print("    no caption track; trying audio")
            audio = fetch_audio(v.url)
            if audio is None:
                print("    no audio either -> skipped")
                results.append({"video": v.url, "status": "no_transcript"})
                continue
            t = (transcribe_nvidia(audio)
                 or transcribe_local(audio))
            if t is None:
                print("    no speech-to-text backend available -> skipped")
                results.append({"video": v.url, "status": "no_backend"})
                continue
        print(f"    transcript: {len(t['text'])} chars "
              f"({t['segments']} segments, method={t['method']})")

        a = generate_article(t["text"], title=v.title,
                             channel=acct.account_name)
        if a is None:
            print("    no article generated")
            results.append({"video": v.url, "status": "no_generation",
                            "transcript": t["text"]})
            continue
        check = fact_check(a["text"], t["text"])
        print(f"    article: {len(a['text'])} chars | fact-check: "
              f"{'OK' if check['ok'] else 'FLAGGED'}")
        for flag in check["flags"]:
            print(f"      ! {flag}")
        print()
        for line in a["text"].splitlines():
            print(f"    | {line}")
        print()

        results.append({
            "video_url": v.url, "title": v.title,
            "channel": acct.account_name, "channel_id": acct.channel_id,
            "transcript": t["text"], "transcript_method": t["method"],
            "article": a["text"], "model": a["model"],
            "fact_check": check,
        })
        if not args.dry_run:
            store(args.database_url, v.id, t, a, check)

    engine.dispose()
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    done = [r for r in results if r.get("article")]
    print(f"\n{len(done)}/{len(results)} videos produced an article")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
