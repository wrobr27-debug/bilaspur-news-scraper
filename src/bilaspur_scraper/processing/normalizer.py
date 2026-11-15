"""Deterministic normalization (`ARCHITECTURE.md` §6.9).

Pure functions; Unicode NFC; Hindi preserved; date precision preserved;
original values never discarded.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, timezone
from typing import Optional

from ..schemas.article import ArticleDraft
from .language import detect_language


def normalize_whitespace(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    normalized = unicodedata.normalize("NFC", text)
    lines = [" ".join(line.split()) for line in normalized.splitlines()]
    lines = [line for line in lines if line]
    return "\n\n".join(lines) if lines else None


def normalize_title(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return " ".join(unicodedata.normalize("NFC", text).split()) or None


def _to_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        # Never assume a timezone: keep as naive; caller stores date only.
        return value
    return value.astimezone(timezone.utc)


def normalize_draft(draft: ArticleDraft) -> ArticleDraft:
    """Return a normalized copy of the draft (pure, deterministic)."""
    from urllib.parse import urlsplit, urlunsplit

    canonical = draft.canonical_url
    if canonical:
        parts = urlsplit(canonical)
        canonical = urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                parts.path, parts.query, ""))
    content_text = normalize_whitespace(draft.content_text)
    summary = normalize_whitespace(draft.summary_from_source)
    language = draft.language
    if language == "und":
        language = detect_language(content_text or summary or draft.title)
    if draft.published_at is not None and draft.published_at.tzinfo is not None:
        published_at = _to_utc(draft.published_at)
    else:
        published_at = draft.published_at
    return draft.model_copy(update={
        "title": normalize_title(draft.title),
        "content_text": content_text,
        "summary_from_source": summary,
        "canonical_url": canonical or draft.original_url,
        "language": language,
        "published_at": published_at,
    })
