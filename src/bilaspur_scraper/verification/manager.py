"""Verification Manager (Control Plane) - `ARCHITECTURE.md` §5.1, D9, D17.

Purpose: decide whether a candidate source is suitable. NOT collection.

- hard 10-request budget per attempt; every outbound request counts
  (robots, homepage, feed candidates, sitemap, redirect hops, retries);
- one host; no recursion; no pagination; no browser (D10);
- no article writes; budget exhaustion -> `disabled`, never `rejected`;
- evidence records requests_consumed / request_budget / browser_used /
  verification_policy_version.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..discovery.engine import extract_listing_links, find_feed_hints
from ..discovery.feeds import parse_feed
from ..discovery.sitemaps import parse_sitemap
from ..fetching.budget import BudgetExhausted, RequestBudget
from ..fetching.fetcher import FetchManager
from ..fetching.robots import RobotsGate
from ..fetching.selector import build_fetcher
from ..schemas.enums import (
    VERIFICATION_POLICY_VERSION,
    VERIFICATION_REQUEST_BUDGET,
    CollectionMethod,
    ErrorCode,
    VerificationMethod,
)
from ..schemas.source import SourceRecord

logger = logging.getLogger(__name__)


@dataclass
class VerificationOutcome:
    source_id: str
    result: str  # 'verified' | 'rejected' | 'disabled'
    blocking_reason: str
    requests_consumed: int
    request_budget: int
    browser_used: bool = False
    verification_policy_version: str = VERIFICATION_POLICY_VERSION
    method: Optional[VerificationMethod] = None
    collection_method: Optional[CollectionMethod] = None
    http_status: Optional[int] = None
    robots_allowed: Optional[bool] = None
    feed_found: Optional[bool] = None
    sitemap_found: Optional[bool] = None
    listing_found: Optional[bool] = None
    feed_url: Optional[str] = None
    sitemap_url: Optional[str] = None
    identity_ok: Optional[bool] = None
    evidence_notes: list[str] = field(default_factory=list)
    sample_entry: Optional[dict] = None

    def evidence_text(self) -> str:
        parts = list(self.evidence_notes)
        if self.sample_entry:
            parts.append(f"sample entry: {self.sample_entry.get('title')!r} "
                         f"-> {self.sample_entry.get('url')}")
        return " | ".join(parts) or "no notes"


ERROR_PAGE_MARKERS = ("page not found", "404", "access denied",
                      "forbidden", "site temporarily unavailable")


class VerificationManager:
    """Budgeted verification workflow for one source.

    `use_browser=True` switches the transport to the Playwright adapter
    (D10). The request budget, step order and outcomes are identical; the
    evidence records `browser_used` so a browser-verified source can never
    be mistaken for an HTTP-verified one.
    """

    def __init__(self, settings, *, use_browser: bool = False) -> None:
        self._settings = settings
        self._use_browser = use_browser
        self._fetcher: Optional[FetchManager] = None
        # Facts learned so far in the current attempt (D17: budget
        # exhaustion must still record everything observed, not wipe it).
        self._learned: dict = {}
        self._notes: list[str] = []
        self._homepage_html: Optional[str] = None

    async def verify(self, source: SourceRecord) -> VerificationOutcome:
        budget = RequestBudget(self._settings.verification_request_budget)
        self._fetcher = build_fetcher(self._settings,
                                      use_browser=self._use_browser)
        self._learned = {}
        self._notes = []
        self._homepage_html = None
        if self._use_browser:
            self._notes.append("browser transport (Chromium, D10 approval)")
        try:
            outcome = await self._verify(source, budget)
            return self._stamp(outcome)
        except BudgetExhausted as exc:
            logger.warning("verification budget exhausted for %s", source.source_id)
            # Approved D9 amendment: the listing check needs no outbound
            # request, so it may finish on the already-fetched homepage
            # before concluding disabled. The request cap is never exceeded.
            listing = self._zero_request_listing_conclusion(source, exc)
            if listing is not None:
                return self._stamp(listing)
            outcome = VerificationOutcome(
                source_id=source.source_id,
                result="disabled",
                blocking_reason=f"budget exhausted before conclusion (E_BUDGET); "
                                f"source left pending/disabled, never rejected",
                requests_consumed=exc.consumed,
                request_budget=exc.cap,
            )
            for key in ("method", "collection_method", "http_status",
                        "robots_allowed", "feed_found", "sitemap_found",
                        "listing_found", "identity_ok"):
                if self._learned.get(key) is not None:
                    setattr(outcome, key, self._learned[key])
            outcome.evidence_notes = self._notes
            return self._stamp(outcome)
        finally:
            await self._fetcher.aclose()

    def _stamp(self, outcome: VerificationOutcome) -> VerificationOutcome:
        """Record the transport in the evidence (D17: browser_used)."""
        outcome.browser_used = self._use_browser
        if self._use_browser:
            note = "browser transport (Chromium, D10 approval)"
            if note not in outcome.evidence_notes:
                outcome.evidence_notes = [note] + list(outcome.evidence_notes)
        return outcome

    async def _verify(self, source: SourceRecord,
                      budget: RequestBudget) -> VerificationOutcome:
        host = urlsplit(source.url).netloc

        # --- 1. robots.txt (counts toward budget) ---------------------------
        gate = RobotsGate(self._fetcher)
        robots = await gate.check(source.url, budget=budget)
        if robots.unavailable:
            return VerificationOutcome(
                source_id=source.source_id, result="disabled",
                blocking_reason="robots.txt unavailable (E_ROBOTS_UNAVAILABLE); "
                                "treated as disallowed; retry later",
                requests_consumed=budget.consumed,
                request_budget=budget.cap,
                robots_allowed=False,
                http_status=robots.status_code,
                evidence_notes=[*self._notes,
                                f"robots fetch failed at {robots.url}"],
            )
        if robots.explicitly_disallowed:
            return VerificationOutcome(
                source_id=source.source_id, result="rejected",
                blocking_reason="robots.txt disallows crawling (E_POLICY)",
                requests_consumed=budget.consumed,
                request_budget=budget.cap,
                robots_allowed=False,
                http_status=robots.status_code,
                evidence_notes=[*self._notes,
                                f"robots at {robots.url} disallows"],
            )
        self._learned["robots_allowed"] = True
        self._learned["http_status"] = robots.status_code
        self._notes.append(f"robots OK at {robots.url}")

        # --- 2. homepage identity check (already counted request below) ----
        homepage = await self._fetcher.fetch(source.url, budget=budget)
        if not homepage.ok:
            code = homepage.error_code or ErrorCode.E_HTTP_CLIENT
            status = homepage.status_code
            if status in (404, 410):
                result, reason = "rejected", f"homepage HTTP {status} (E_HTTP_CLIENT)"
            elif code in (ErrorCode.E_HTTP_CLIENT,):
                result, reason = "disabled", f"homepage HTTP {status}; blocked (E_HTTP_CLIENT)"
            else:
                result, reason = "disabled", \
                    f"homepage unreachable ({code.value}); temporary failure"
            return VerificationOutcome(
                source_id=source.source_id, result=result, blocking_reason=reason,
                requests_consumed=budget.consumed, request_budget=budget.cap,
                robots_allowed=True, http_status=status,
                evidence_notes=[f"homepage fetch failed: {reason}"],
            )
        identity_ok = self._identity_ok(source, homepage, host)
        if not identity_ok:
            return VerificationOutcome(
                source_id=source.source_id, result="rejected",
                blocking_reason="identity mismatch: final URL host differs from "
                                "registry URL host",
                requests_consumed=budget.consumed, request_budget=budget.cap,
                robots_allowed=True, http_status=homepage.status_code,
                identity_ok=False,
                evidence_notes=[f"redirected to {homepage.final_url}"],
            )

        self._learned["http_status"] = homepage.status_code
        self._learned["identity_ok"] = True
        self._notes.append(
            f"homepage OK ({homepage.status_code}) at {homepage.final_url}")

        html = homepage.text()
        self._homepage_html = html
        outcome = VerificationOutcome(
            source_id=source.source_id, result="rejected",
            blocking_reason="no collectable content found within budget",
            requests_consumed=budget.consumed, request_budget=budget.cap,
            robots_allowed=True, http_status=homepage.status_code,
            identity_ok=True,
            evidence_notes=self._notes,  # shared list: survives budget stop
        )

        # --- 3. feed candidates (<=5 fetches incl. hints) --------------------
        candidates = find_feed_hints(html, source.url)
        for path in ("/rss.xml", "/feed", "/rss", "/atom.xml"):
            from urllib.parse import urljoin
            candidates.append(urljoin(source.url, path))
        seen: set[str] = set()
        unique_candidates = []
        for c in candidates:
            key = c.split("?")[0].rstrip("/")
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)
        for candidate in unique_candidates[:5]:
            if budget.remaining <= 1:
                outcome.evidence_notes.append("budget stop before more feed probes")
                break
            feed_result = await self._fetcher.fetch(candidate, budget=budget)
            if not feed_result.ok:
                outcome.evidence_notes.append(
                    f"feed probe failed {candidate} ({feed_result.error_code})")
                continue
            content_type = (feed_result.header("content-type") or "").lower()
            if "html" in content_type and "xml" not in content_type \
                    and "rss" not in content_type:
                outcome.evidence_notes.append(f"feed probe is HTML {candidate}")
                continue
            parsed = parse_feed(feed_result)
            if parsed.ok and parsed.entries:
                outcome.result = "verified"
                outcome.blocking_reason = ""
                outcome.method = VerificationMethod.RSS
                outcome.collection_method = CollectionMethod.RSS
                outcome.feed_found = True
                outcome.feed_url = parsed.feed_url
                sample = parsed.entries[0]
                outcome.sample_entry = {"title": sample.title, "url": sample.url}
                outcome.evidence_notes.append(
                    f"feed OK at {parsed.feed_url} "
                    f"({len(parsed.entries)} entries)")
                return outcome
            outcome.evidence_notes.append(f"feed unusable {candidate}: {parsed.error}")

        # --- 4. sitemap (robots Sitemap: directive or /sitemap.xml) ---------
        sitemap_candidates = [s for s in (robots.sitemaps or [])
                              if urlsplit(s).netloc == host][:1]
        sitemap_candidates.append(f"https://{host}/sitemap.xml")
        for candidate in sitemap_candidates[:2]:
            if budget.remaining <= 1:
                outcome.evidence_notes.append("budget stop before sitemap probes")
                break
            sm_result = await self._fetcher.fetch(candidate, budget=budget)
            if not sm_result.ok:
                outcome.evidence_notes.append(f"sitemap probe failed {candidate}")
                continue
            parsed = parse_sitemap(sm_result)
            if parsed.kind == "sitemapindex" and parsed.child_sitemaps \
                    and budget.remaining > 1:
                child = await self._fetcher.fetch(parsed.child_sitemaps[0],
                                                  budget=budget)
                if child.ok:
                    parsed = parse_sitemap(child)
            if parsed.ok and parsed.urls:
                outcome.result = "verified"
                outcome.blocking_reason = ""
                outcome.method = VerificationMethod.SITEMAP
                outcome.collection_method = CollectionMethod.SITEMAP
                outcome.sitemap_found = True
                outcome.sitemap_url = candidate
                outcome.evidence_notes.append(
                    f"sitemap OK at {candidate} ({len(parsed.urls)} URLs)")
                return outcome
            outcome.evidence_notes.append(f"sitemap unusable {candidate}: "
                                          f"{parsed.error}")

        # --- 5. listing: homepage already fetched - parse article links -----
        links = extract_listing_links(html, source.url, max_items=20)
        if len(links) >= 3:
            outcome.result = "verified"
            outcome.blocking_reason = ""
            outcome.method = VerificationMethod.LISTING
            outcome.collection_method = CollectionMethod.HTML
            outcome.listing_found = True
            outcome.evidence_notes.append(
                f"listing OK on homepage ({len(links)} article links)")
            return outcome
        outcome.evidence_notes.append(f"listing weak ({len(links)} links)")

        # --- 6. conclusion ----------------------------------------------------
        if not outcome.feed_found and not outcome.sitemap_found \
                and not outcome.listing_found:
            title = ""
            soup = BeautifulSoup(html, "html.parser")
            if soup.title and soup.title.string:
                title = " ".join(soup.title.string.split())
            if any(marker in title.lower() for marker in ERROR_PAGE_MARKERS):
                outcome.blocking_reason = \
                    f"homepage is an error page (title={title!r})"
        return outcome

    def _zero_request_listing_conclusion(
            self, source: SourceRecord, exc: BudgetExhausted,
    ) -> Optional[VerificationOutcome]:
        """Approved D9 amendment: finish the listing check with zero
        additional requests on the already-fetched homepage.

        Returns a `verified` outcome when the homepage carries >= 3 article
        links; otherwise `None` (caller concludes `disabled` as before).
        No outbound request is ever issued here, so the 10-request cap is
        never exceeded.
        """
        if not self._homepage_html:
            return None
        self._notes.append(
            f"budget exhausted after {exc.consumed}/{exc.cap} requests")
        links = extract_listing_links(self._homepage_html, source.url,
                                      max_items=20)
        if len(links) < 3:
            self._notes.append(
                f"zero-request listing check weak ({len(links)} links); "
                "no additional requests issued")
            return None
        self._notes.append(
            f"zero-request listing check OK ({len(links)} article links; "
            "no additional requests issued)")
        return VerificationOutcome(
            source_id=source.source_id, result="verified", blocking_reason="",
            requests_consumed=exc.consumed, request_budget=exc.cap,
            method=VerificationMethod.LISTING,
            collection_method=CollectionMethod.HTML,
            robots_allowed=self._learned.get("robots_allowed"),
            http_status=self._learned.get("http_status"),
            identity_ok=self._learned.get("identity_ok"),
            listing_found=True,
            evidence_notes=self._notes,
        )

    def _identity_ok(self, source: SourceRecord, homepage, host: str) -> bool:
        final_host = urlsplit(homepage.final_url).netloc.lower()
        if final_host and final_host != host.lower():
            # Allow www.<host> equivalence only.
            if final_host.removeprefix("www.") != host.lower().removeprefix("www."):
                return False
        if homepage.status_code and homepage.status_code >= 400:
            return False
        return True


def run_verification(settings, source: SourceRecord, *,
                     use_browser: bool = False) -> VerificationOutcome:
    """Sync entry point used by the CLI."""
    manager = VerificationManager(settings, use_browser=use_browser)
    return asyncio.run(manager.verify(source))
