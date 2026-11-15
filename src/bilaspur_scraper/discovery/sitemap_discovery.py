"""Sitemap-based article discovery (D19).

A publisher sitemap is the publisher's own machine-readable index of
its articles - sanctioned, cheap, and far more complete than a listing
page that only shows the latest dozen items. This module turns a sitemap
into article discovery items.

Two sitemap dialects matter here:

* **plain sitemap** (`<urlset>`): `<loc>` plus optional `<lastmod>`.
* **Google News sitemap** (`<urlset>` + `xmlns:news`): adds
  `<news:title>` and `<news:publication_date>`, so the headline and
  the publication date are known *before* the article page is fetched.
  That saves a request and lets the pipeline skip pages it has already
  seen.

The module also handles a **sitemap index** (`<sitemapindex>`) by
following one level of child sitemaps - WordPress (`wp-sitemap.xml`),
Yoast and many publishers use that shape.

Only sitemaps the publisher advertises (in `robots.txt`) or publishes at
a standard path are read; nothing here crawls the site to find them.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..fetching.fetcher import FetchResult

logger = logging.getLogger(__name__)

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_NEWS_NS = "http://www.google.com/schemas/sitemap-news/0.9"

#: Standard sitemap locations, tried in this order when robots.txt does
#: not advertise one. Publishers overwhelmingly use one of these.
COMMON_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/news-sitemap.xml",
    "/post-sitemap.xml",
    "/wp-sitemap.xml",
    "/sitemap-news.xml",
    "/sitemap.php",
)


@dataclass
class SitemapEntry:
    """One article URL, with whatever the sitemap already told us."""
    url: str
    lastmod: Optional[str] = None
    news_title: Optional[str] = None
    news_published: Optional[str] = None

    @property
    def published_at(self) -> Optional[datetime]:
        """The publication timestamp, if the sitemap supplied one."""
        if not self.news_published:
            return None
        text = self.news_published.strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


@dataclass
class SitemapIndexResult:
    """The outcome of reading one sitemap (and any children it names)."""
    ok: bool
    kind: str = "none"          # 'urlset' | 'sitemapindex' | 'none'
    entries: list[SitemapEntry] = field(default_factory=list)
    child_sitemaps: list[str] = field(default_factory=list)
    sitemaps_read: list[str] = field(default_factory=list)
    error: str = ""


def _local_name(elem) -> str:
    """The tag name without its namespace."""
    tag = elem.tag
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def _find_child(elem, name: str):
    """Find a direct child by local name, ignoring the namespace.

    Publishers are inconsistent: the sitemap namespace is sometimes
    `http://www.sitemaps.org/...` and sometimes
    `https://www.sitemaps.org/...` (nai-dunia uses the latter), and
    some documents declare no namespace at all. Matching on the local
    name handles all three.
    """
    if elem is None:
        return None
    for child in elem:
        if _local_name(child) == name:
            return child
    return None


def _text(elem, name: str, _ns: str = _SITEMAP_NS) -> Optional[str]:
    if elem is None:
        return None
    child = _find_child(elem, name)
    if child is None:
        return None
    value = (child.text or "").strip()
    return value or None


def parse_sitemap_entries(result: FetchResult) -> SitemapIndexResult:
    """Parse a fetched sitemap into entries.

    Accepts both `<urlset>` and `<sitemapindex>`. A malformed document
    yields ok=False with the reason, never an exception.
    """
    body = result.body or b""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return SitemapIndexResult(
            ok=False, error=f"malformed sitemap XML: {exc}")
    except Exception as exc:  # noqa: BLE001
        return SitemapIndexResult(ok=False, error=f"unreadable sitemap: {exc}")

    tag = _local_name(root)

    if tag == "sitemapindex":
        children = []
        for sm in root:
            if _local_name(sm) != "sitemap":
                continue
            loc = _text(sm, "loc")
            if loc:
                children.append(loc)
        return SitemapIndexResult(ok=True, kind="sitemapindex",
                                  child_sitemaps=children,
                                  sitemaps_read=[result.url])

    if tag != "urlset":
        return SitemapIndexResult(ok=False,
                                  error=f"unrecognized sitemap root {tag!r}")

    entries: list[SitemapEntry] = []
    for url_elem in root:
        if _local_name(url_elem) != "url":
            continue
        loc = _text(url_elem, "loc")
        if not loc:
            continue
        news = _find_child(url_elem, "news")
        news_title = None
        news_published = None
        if news is not None:
            news_title = _text(news, "title")
            news_published = _text(news, "publication_date")
        entries.append(SitemapEntry(
            url=loc,
            lastmod=_text(url_elem, "lastmod"),
            news_title=news_title,
            news_published=news_published,
        ))
    return SitemapIndexResult(ok=True, kind="urlset", entries=entries,
                              sitemaps_read=[result.url])
