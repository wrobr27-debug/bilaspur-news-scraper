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
from ..processing.bilaspur_match import (
    MatchResult,
    match_bilaspur,
    scope_prefix,
)
from .feeds import FeedEntry, FeedParseResult, parse_feed
from .sitemap_discovery import (
    COMMON_SITEMAP_PATHS,
    SitemapEntry,
    parse_sitemap_entries,
)

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

    def __init__(self, fetcher: FetchManager, page_fetcher=None, *,
                 max_sitemap_reads: int = 4,
                 max_child_sitemaps: int = 3,
                 max_feed_probes: int = 3,
                 allow_localities: bool = True,
                 source_scope: str | None = None) -> None:
        self._fetcher = fetcher
        self._page_fetcher = page_fetcher or fetcher
        self._robots_sitemaps: list[str] = []
        # Bounds so one sitemap index cannot fan out into hundreds of
        # requests (politeness, and the verification budget).
        self._max_sitemap_reads = max_sitemap_reads
        self._max_child_sitemaps = max_child_sitemaps
        self._max_feed_probes = max_feed_probes
        self._allow_localities = allow_localities
        # The source's declared coverage scope. A city/district source
        # publishes Bilaspur articles whose URLs need not name it.
        self._source_scope = source_scope

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
        # The source's own Bilaspur section, if its registry URL names
        # one. A national publisher's site-wide sitemap must be filtered
        # down to that section, or it yields articles from every state.
        prefix = scope_prefix(base_url)

        # --- 1. RSS/Atom ---------------------------------------------------
        # The homepage is fetched once and reused for feed hints and for
        # the listing fallback later.
        feed_candidates: list[str] = []
        if prefer_feed_url:
            feed_candidates.append(prefer_feed_url)
        homepage = await self._page_fetcher.fetch(base_url, budget=budget)
        if homepage.ok:
            feed_candidates.extend(find_feed_hints(homepage.text(), base_url))
        parts = urlsplit(base_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        for path in COMMON_FEED_PATHS:
            feed_candidates.append(urljoin(origin, path))
        seen: set[str] = set()
        deduped_candidates = []
        for candidate in feed_candidates:
            key = candidate.split("?")[0].rstrip("/")
            if key not in seen:
                seen.add(key)
                deduped_candidates.append(candidate)
        for candidate in deduped_candidates[:self._max_feed_probes]:
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
                # A national publisher's feed carries every state. Keep
                # only the entries whose URL identifies Bilaspur, so the
                # pipeline never fetches an article from another state.
                kept = []
                for e in parsed.entries:
                    verdict = match_bilaspur(
                        e.url, host=host,
                        allow_localities=self._allow_localities,
                        source_scope=self._source_scope,
                        prefix=prefix)
                    if verdict.matched:
                        kept.append((e, verdict))
                if not kept:
                    outcome.errors.append(
                        f"feed {parsed.feed_url}: {len(parsed.entries)} entries, "
                        f"none Bilaspur-relevant")
                    continue
                outcome.strategy_used = "rss"
                outcome.feed_url = parsed.feed_url
                outcome.items = [
                    DiscoveryItem(
                        url=e.url,
                        method="rss",
                        evidence=(f"entry in feed {parsed.feed_url} "
                                  f"({verdict.reason})"),
                        discovered_at=datetime.now(timezone.utc),
                        title_hint=e.title,
                        published_hint=e.published_date or e.published_at_original,
                        summary_hint=e.summary,
                    )
                    for e, verdict in kept[:max_items]
                ]
                return outcome
            outcome.errors.append(f"feed parse failed or empty: {candidate} "
                                  f"{parsed.error}")

        if feed_only:
            return outcome

        # --- 2. Sitemap ----------------------------------------------------
        # A publisher sitemap is the publisher's own index of its
        # articles: sanctioned, cheap, and far more complete than a
        # listing page (D19). Sitemaps the publisher advertises in
        # robots.txt come first; the standard paths are tried next.
        #
        # Sitemaps live at the *site root*, not under the source's
        # section path, so they are built from the origin, not from
        # `base_url`.
        parts = urlsplit(base_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        sitemap_candidates = [s for s in self._robots_sitemaps
                              if _same_host(s, host)]
        for path in COMMON_SITEMAP_PATHS:
            sitemap_candidates.append(urljoin(origin, path))
        seen_sitemaps: set[str] = set()
        tried = 0
        for candidate in sitemap_candidates:
            if tried >= self._max_sitemap_reads:
                break
            key = candidate.split("?")[0].rstrip("/")
            if key in seen_sitemaps:
                continue
            seen_sitemaps.add(key)
            tried += 1
            sitemap_result = await self._fetcher.fetch(candidate, budget=budget)
            if not sitemap_result.ok:
                outcome.errors.append(f"sitemap fetch failed: {candidate}")
                continue
            parsed = parse_sitemap_entries(sitemap_result)
            if not parsed.ok:
                outcome.errors.append(f"sitemap parse failed: {candidate} "
                                      f"{parsed.error}")
                continue
            # A sitemap index names child sitemaps; follow a few of them.
            if parsed.kind == "sitemapindex" and parsed.child_sitemaps:
                for child_url in parsed.child_sitemaps[:self._max_child_sitemaps]:
                    if not _same_host(child_url, host):
                        continue
                    child = await self._fetcher.fetch(child_url, budget=budget)
                    if not child.ok:
                        continue
                    child_parsed = parse_sitemap_entries(child)
                    if not child_parsed.ok:
                        continue
                    parsed.entries.extend(child_parsed.entries)
                    parsed.sitemaps_read.extend(child_parsed.sitemaps_read)
                    if len(parsed.entries) >= max_items * 4:
                        break
            if not parsed.entries:
                continue

            # Keep only URLs whose *path* identifies Bilaspur coverage.
            # A naive substring search would also match Bilaspur in
            # Himachal Pradesh.
            matched: list[tuple[SitemapEntry, MatchResult]] = []
            for entry in parsed.entries:
                if not ARTICLE_PATH_RE.search(urlsplit(entry.url).path or ""):
                    continue
                verdict = match_bilaspur(entry.url, host=host,
                                         allow_localities=self._allow_localities,
                                         source_scope=self._source_scope,
                                         prefix=prefix)
                if verdict.matched:
                    matched.append((entry, verdict))
            if not matched:
                outcome.errors.append(
                    f"sitemap {candidate}: {len(parsed.entries)} URLs, "
                    f"none Bilaspur-relevant")
                continue

            outcome.strategy_used = "sitemap"
            outcome.sitemap_url = candidate
            items = []
            for entry, verdict in matched[:max_items]:
                items.append(DiscoveryItem(
                    url=entry.url,
                    method="sitemap",
                    evidence=(f"URL in sitemap {candidate} "
                              f"({verdict.reason})"),
                    discovered_at=datetime.now(timezone.utc),
                    title_hint=entry.news_title,
                    published_hint=(entry.news_published or entry.lastmod),
                    summary_hint=None,
                ))
            outcome.items = items
            return outcome

        # --- 3. Listing ------------------------------------------------------
        if homepage.ok:
            links = extract_listing_links(homepage.text(), base_url,
                                          max_items=max_items * 3)
            # The listing is the loosest signal: a homepage links to
            # other states, to login pages, to promos. Keep only URLs
            # that carry a Bilaspur district signal.
            kept: list[tuple[str, MatchResult]] = []
            for link in links:
                verdict = match_bilaspur(
                    link, host=host,
                    allow_localities=self._allow_localities,
                    source_scope=self._source_scope,
                    prefix=prefix)
                if verdict.matched:
                    kept.append((link, verdict))
            if kept:
                outcome.strategy_used = "listing"
                outcome.items = [
                    DiscoveryItem(
                        url=link, method="listing",
                        evidence=("article link on listing/home page "
                                  f"({verdict.reason})"),
                        discovered_at=datetime.now(timezone.utc),
                    )
                    for link, verdict in kept[:max_items]
                ]
        else:
            outcome.errors.append("homepage unavailable for listing discovery")
        return outcome
