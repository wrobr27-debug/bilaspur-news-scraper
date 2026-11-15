"""Deduplication signals (`PLAN.md` §8, `SKILLS.md` §8).

Phase 1 implements signals 1-3 (canonical URL, normalized URL, content
hash). `content_hash` is a signal, never a global uniqueness rule.
"""

from __future__ import annotations

import hashlib
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content", "fbclid", "gclid", "ref", "ref_src"}


def normalize_url(url: str) -> str:
    """Strip tracking params/fragment, normalize scheme/host, drop
    trailing slash (path case preserved)."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        path,
        urlencode(query),
        "",
    ))


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text or "")
    return " ".join(normalized.split()).lower()


def content_hash(title: str | None, body: str | None,
                 published_date: str | None) -> str:
    """SHA-256 over normalized title + content + date (versioned recipe)."""
    parts = [
        _normalize_text(title or ""),
        _normalize_text(body or ""),
        published_date or "",
    ]
    return "sha256:" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
