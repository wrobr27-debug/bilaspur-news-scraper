"""Feed parsing (RSS/Atom) via feedparser (`ARCHITECTURE.md` §12)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import feedparser

from ..fetching.fetcher import FetchResult


@dataclass
class FeedEntry:
    url: str
    title: Optional[str]
    published_at: Optional[datetime]
    published_date: Optional[str]
    published_at_original: Optional[str]
    summary: Optional[str]
    author: Optional[str]
    tags: list[str]


@dataclass
class FeedParseResult:
    ok: bool
    feed_url: str
    feed_title: Optional[str]
    entries: list[FeedEntry]
    error: str = ""


def _entry_date(entry) -> tuple[Optional[datetime], Optional[str], Optional[str]]:
    original = None
    for attr in ("published", "updated"):
        original = getattr(entry, attr, None)
        if original:
            break
    parsed = getattr(entry, "published_parsed", None) \
        or getattr(entry, "updated_parsed", None)
    if parsed:
        dt = datetime(*parsed[:6], tzinfo=timezone.utc)
        return dt, dt.date().isoformat(), original
    if original:
        # Date string without machine-parseable part: keep as original only.
        return None, None, original
    return None, None, None


def parse_feed(result: FetchResult) -> FeedParseResult:
    """Parse a fetched feed. Malformed feeds yield ok=False with error."""
    body = result.body or b""
    parsed = feedparser.parse(body)
    bozo = getattr(parsed, "bozo", 0)
    entries = []
    for entry in parsed.entries:
        link = getattr(entry, "link", None)
        if not link:
            continue
        published_at, published_date, original = _entry_date(entry)
        tags = [t.get("term", "") for t in getattr(entry, "tags", []) or []]
        entries.append(FeedEntry(
            url=link,
            title=getattr(entry, "title", None),
            published_at=published_at,
            published_date=published_date,
            published_at_original=original,
            summary=getattr(entry, "summary", None) or getattr(entry, "description", None),
            author=getattr(entry, "author", None),
            tags=[t for t in tags if t],
        ))
    title = getattr(parsed.feed, "title", None) if parsed.feed else None
    if not entries and bozo:
        return FeedParseResult(
            ok=False, feed_url=result.url, feed_title=title,
            entries=[], error=f"malformed feed ({getattr(parsed, 'bozo_exception', '?')})"
        )
    return FeedParseResult(
        ok=True, feed_url=result.url, feed_title=title, entries=entries,
    )
