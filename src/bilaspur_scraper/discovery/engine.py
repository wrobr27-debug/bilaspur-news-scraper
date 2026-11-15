"""Discovery engine - feed first, then sitemap, then listing
(`ARCHITECTURE.md` §12, D11: feed-only on normal runs when available)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..fetching.fetcher import FetchManager, FetchResult
from .feeds import FeedEntry, FeedParseResult, parse_feed
from .sitemaps import SitemapResult, parse_sitemap

logger = logging.getLogger(__name__)

#: URL shapes that identify a news article.
#:
#: The keyword may be a whole path segment (`/news/story`) or the tail
#: of a compound segment (`/bilaspur-news/story`). A *bare* keyword
#: segment (`/state-news`, `/national-news`) is a section index, not an
#: article, so a bare segment never matches on its own - an article URL
#: must have something after the keyword (a slug, a date path, or a
#: numeric id).
ARTICLE_PATH_RE = re.compile(
    r"/(news|article|stories|story|notice|tender|press|circular|order|"
    r"whats-new|news-detail|content|post|blog|vlogs)"
    r"[-/][^/]+/"          # /news/slug/  or  /bilaspur-news/slug/
    r"|/(news|article|stories|story|notice|tender|press|circular|order|"
    r"whats-new|news-detail|content|post|blog|vlogs)"
    r"[-/][^/]+$"          # /news/slug   (no trailing slash)
    r"|/20\d\d/\d\d?/"     # /2026/11/
    r"|/\d{4,}/"           # /12345/ numeric id
    r"|\.(html|htm|php)$",
    re.IGNORECASE,
)
#: Paths that are never articles even when they contain a news-ish word.
#: Only *section index* names belong here. Words like "explainer" or
#: "viral" are NOT listed: publishers use them as real article
#: categories (`/explainer/news/some-story`), so excluding them throws
#: away genuine articles.
NON_ARTICLE_RE = re.compile(
    r"\.(jpg|jpeg|png|gif|pdf|css|js|ico|svg|zip|xml|json)([?#]|$)|"
    r"/(category|categories|tag|tags|author|authors|page|pages|search|"
    r"login|feed|rss|atom|privacy|terms|about|contact|advertisement|"
    r"subscribe|newsletter|events?|photo|gallery|web-stories)([-/]|$)",
    re.IGNORECASE,
)
COMMON_FEED_PATHS = [
    "/rss.xml",       # Drupal (common on gov portals)
    "/feed",          # WordPress
    "/rss",
    "/atom.xml",
]


@dataclass
class DiscoveryItem:
    url: str
    method: str  # 'rss' | 'sitemap' | 'listing'
    evidence: str
    discovered_at: datetime
    title_hint: Optional[str] = None
    published_hint: Optional[str] = None
    summary_hint: Optional[str] = None


@dataclass
class DiscoveryOutcome:
    strategy_used: str  # 'rss' | 'sitemap' | 'listing' | 'none'
    feed_url: Optional[str] = None
    sitemap_url: Optional[str] = None
    items: list[DiscoveryItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _same_host(url: str, host: str) -> bool:
    return urlsplit(url).netloc.lower() == host.lower()


def find_feed_hints(html: str, base_url: str) -> list[str]:
    """<link rel="alternate"> feed hints from a fetched page."""
    soup = BeautifulSoup(html, "html.parser")
    hints: list[str] = []
    for link in soup.find_all("link", rel="alternate"):
        types = link.get("type", "") or ""
        if "rss" in str(types).lower() or "atom" in str(types).lower():
            href = link.get("href")
            if href:
                hints.append(urljoin(base_url, href.strip()))
    return hints


def extract_listing_links(html: str, base_url: str, *, max_items: int) -> list[str]:
    """Plausible article links from a listing/home page."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[str] = []
    host = urlsplit(base_url).netloc
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"].strip())
        if not _same_host(href, host):
            continue
        path = urlsplit(href).path or "/"
        if NON_ARTICLE_RE.search(path):
            continue
        if not ARTICLE_PATH_RE.search(path):
            continue
        if href.rstrip("/") in seen or href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= max_items:
            break
    return links


class DiscoveryEngine:
    """Order: RSS/Atom -> sitemap -> listing. Each failure falls through.

    `page_fetcher` is the fetcher used for HTML pages (homepage, and any
    page whose links are injected by JavaScript). It defaults to the main
    fetcher; for a browser-approved source the pipeline passes the browser
    here while feeds and sitemaps stay on the cheap HTTP transport (D11).
    """

    def __init__(self, fetcher: FetchManager, page_fetcher=None) -> None:
        self._fetcher = fetcher
        self._page_fetcher = page_fetcher or fetcher
        self._robots_sitemaps: list[str] = []

    def set_robots_sitemaps(self, sitemaps: list[str] | None) -> None:
        self._robots_sitemaps = list(sitemaps or [])

    async def discover(
        self,
        base_url: str,
        *,
        budget=None,
        prefer_feed_url: Optional[str] = None,
        max_items: int = 10,
        feed_only: bool = True,
    ) -> DiscoveryOutcome:
        host = urlsplit(base_url).netloc
        outcome = DiscoveryOutcome(strategy_used="none")

        # --- 1. RSS/Atom ---------------------------------------------------
        feed_candidates: list[str] = []
        if prefer_feed_url:
            feed_candidates.append(prefer_feed_url)
        homepage = await self._page_fetcher.fetch(base_url, budget=budget)
        if homepage.ok:
            feed_candidates.extend(find_feed_hints(homepage.text(), base_url))
        for path in COMMON_FEED_PATHS:
            candidate = urljoin(f"https://{host}" if not base_url.startswith("http")
                                else base_url, path)
            feed_candidates.append(urljoin(base_url if base_url.startswith("http")
                                           else f"https://{host}", path))
        seen: set[str] = set()
        deduped_candidates = []
        for candidate in feed_candidates:
            key = candidate.split("?")[0].rstrip("/")
            if key not in seen:
                seen.add(key)
                deduped_candidates.append(candidate)
        for candidate in deduped_candidates[:5]:
            if budget is not None and budget.remaining <= 1:
                outcome.errors.append("budget stop before feed candidates")
                break
            feed_result = await self._fetcher.fetch(candidate, budget=budget)
            if not feed_result.ok:
                outcome.errors.append(f"feed fetch failed: {candidate} "
                                      f"({feed_result.error_code})")
                continue
            content_type = (feed_result.header("content-type") or "").lower()
            if "html" in content_type and "xml" not in content_type \
                    and "rss" not in content_type:
                outcome.errors.append(f"feed candidate is HTML: {candidate}")
                continue
            parsed = parse_feed(feed_result)
            if parsed.ok and parsed.entries:
                outcome.strategy_used = "rss"
                outcome.feed_url = parsed.feed_url
                outcome.items = [
                    DiscoveryItem(
                        url=e.url,
                        method="rss",
                        evidence=f"entry in feed {parsed.feed_url}",
                        discovered_at=datetime.now(timezone.utc),
                        title_hint=e.title,
                        published_hint=e.published_date or e.published_at_original,
                        summary_hint=e.summary,
                    )
                    for e in parsed.entries[:max_items]
                ]
                return outcome
            outcome.errors.append(f"feed parse failed or empty: {candidate} "
                                  f"{parsed.error}")

        if feed_only:
            return outcome

        # --- 2. Sitemap ----------------------------------------------------
        sitemap_candidates = [s for s in self._robots_sitemaps if _same_host(s, host)]
        sitemap_candidates.append(urljoin(base_url, "/sitemap.xml"))
        for candidate in sitemap_candidates[:2]:
            sitemap_result = await self._fetcher.fetch(candidate, budget=budget)
            if not sitemap_result.ok:
                outcome.errors.append(f"sitemap fetch failed: {candidate}")
                continue
            parsed = parse_sitemap(sitemap_result)
            if not parsed.ok:
                outcome.errors.append(f"sitemap parse failed: {candidate} "
                                      f"{parsed.error}")
                continue
            if parsed.kind == "sitemapindex" and parsed.child_sitemaps:
                child = await self._fetcher.fetch(parsed.child_sitemaps[0],
                                                  budget=budget)
                if child.ok:
                    parsed = parse_sitemap(child)
                    if not parsed.ok:
                        outcome.errors.append("child sitemap parse failed")
                        continue
            if parsed.urls:
                outcome.strategy_used = "sitemap"
                outcome.sitemap_url = candidate
                outcome.items = [
                    DiscoveryItem(
                        url=u, method="sitemap",
                        evidence=f"URL in sitemap {candidate}",
                        discovered_at=datetime.now(timezone.utc),
                    )
                    for u in parsed.urls
                    if ARTICLE_PATH_RE.search(urlsplit(u).path or "")
                ][:max_items]
                return outcome

        # --- 3. Listing ------------------------------------------------------
        if homepage.ok:
            links = extract_listing_links(homepage.text(), base_url,
                                          max_items=max_items)
            if links:
                outcome.strategy_used = "listing"
                outcome.items = [
                    DiscoveryItem(
                        url=link, method="listing",
                        evidence="article link on listing/home page",
                        discovered_at=datetime.now(timezone.utc),
                    )
                    for link in links
                ]
        else:
            outcome.errors.append("homepage unavailable for listing discovery")
        return outcome
