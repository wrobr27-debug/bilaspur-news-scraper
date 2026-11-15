"""Verification Manager tests - fully offline via a scripted stub fetcher.

Covers the engine's step ordering (robots -> homepage -> feed -> sitemap ->
listing) and the D17 evidence rule: a budget stop must still report
everything learned before the budget ran out. No network access.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from bilaspur_scraper.config.settings import Settings
from bilaspur_scraper.fetching.budget import BudgetExhausted
from bilaspur_scraper.fetching.fetcher import FetchResult
from bilaspur_scraper.schemas.enums import (
    CollectionMethod,
    CoverageScope,
    Priority,
    SourceType,
    UrlKind,
    VerificationMethod,
)
from bilaspur_scraper.schemas.source import SourceRecord
from bilaspur_scraper.verification.manager import VerificationManager

BASE = "https://example.gov.in/"
ROBOTS_URL = BASE + "robots.txt"

ROBOTS_ALLOW = b"User-agent: *\nAllow: /\n"
ROBOTS_DISALLOW = b"User-agent: *\nDisallow: /\n"
PLAIN_HOMEPAGE = (
    b"<html><head><title>District Administration</title></head>"
    b"<body><p>hello</p></body></html>"
)
LISTING_HOMEPAGE = (
    b"<html><head><title>District Administration</title></head><body>"
    b'<a href="/press/notice-a.html">Notice A</a>'
    b'<a href="/news/tender-b.html">Tender B</a>'
    b'<a href="/whats-new/circular-c.html">Circular C</a>'
    b"</body></html>"
)
RSS_FEED = (
    b'<?xml version="1.0"?>\n'
    b'<rss version="2.0"><channel><title>Example Gov</title>'
    b"<link>https://example.gov.in/</link><description>notices</description>"
    b"<item><title>Notice A</title>"
    b"<link>https://example.gov.in/press/notice-a.html</link>"
    b"<pubDate>Mon, 09 Nov 2026 10:00:00 +0530</pubDate></item>"
    b"</channel></rss>"
)


def make_settings(budget: int = 10) -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        user_agent="BilaspurNewsBot/0.1 (test)",
        contact_email="test@example.com",
        request_timeout_s=5,
        max_concurrency=1,
        per_domain_delay_s=0.0,
        max_retries=3,
        backoff_base_s=0.0,
        verification_request_budget=budget,
        browser_crawling_enabled=False,
        respect_robots=True,
        dry_run=True,
        log_level="INFO",
    )


def make_source() -> SourceRecord:
    return SourceRecord(
        source_id="example-source",
        source_name="Example Source",
        source_type=SourceType.DISTRICT_GOVERNMENT,
        url=BASE,
        url_kind=UrlKind.HOMEPAGE,
        coverage_scope=CoverageScope.DISTRICT,
        priority=Priority.P1,
        origin_sheet="Institutions & Business",
        origin_row=1,
    )


def make_result(url: str, body: bytes = b"", status: int = 200,
                content_type: str = "text/html") -> FetchResult:
    return FetchResult(
        url=url, final_url=url, status_code=status,
        headers={"content-type": content_type}, body=body,
        fetched_at=datetime.now(timezone.utc), duration_ms=1,
    )


def not_found(url: str) -> FetchResult:
    return FetchResult(
        url=url, final_url=url, status_code=404, headers={}, body=b"",
        fetched_at=datetime.now(timezone.utc), duration_ms=1,
        error_code=None, error_message="HTTP 404",
    )


class StubFetcher:
    """Scripted fetcher: maps exact URLs to results or raises.

    Mirrors the real fetcher's budget accounting: every fetch consumes
    exactly one counted request before returning (D9).
    """

    def __init__(self, script: dict[str, object]):
        self.script = script
        self.requested: list[str] = []

    @property
    def user_agent(self) -> str:
        return "BilaspurNewsBot/0.1"

    async def fetch(self, url, *, budget=None, **kwargs) -> FetchResult:
        self.requested.append(url)
        if budget is not None:
            budget.consume()  # raises BudgetExhausted past the cap
        if url not in self.script:
            raise KeyError(f"unscripted URL {url!r}; scripted keys: "
                           f"{sorted(map(repr, self.script))}")
        outcome = self.script[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        return None


def patch_fetcher(monkeypatch, stub: StubFetcher) -> None:
    import bilaspur_scraper.verification.manager as manager_mod

    monkeypatch.setattr(manager_mod, "build_fetcher",
                        lambda settings, use_browser=False: stub)


def script_all_probes_missing() -> dict[str, object]:
    script: dict[str, object] = {
        ROBOTS_URL: make_result(ROBOTS_URL, ROBOTS_ALLOW,
                                content_type="text/plain"),
        BASE: make_result(BASE, PLAIN_HOMEPAGE),
    }
    for path in ("/rss.xml", "/feed", "/rss", "/atom.xml", "/sitemap.xml"):
        url = BASE.rstrip("/") + path  # match urljoin: no double slash
        script[url] = not_found(url)
    return script


def test_budget_exhaustion_preserves_learned_evidence(monkeypatch) -> None:
    """D17: a budget stop must not wipe robots/homepage facts."""
    stub = StubFetcher({
        ROBOTS_URL: make_result(ROBOTS_URL, ROBOTS_ALLOW,
                                content_type="text/plain"),
        BASE: make_result(BASE, PLAIN_HOMEPAGE),
        # Probe 1: budget dies mid-flow (retries would exhaust it for real).
        BASE + "rss.xml": BudgetExhausted(consumed=3, cap=10),
    })
    patch_fetcher(monkeypatch, stub)

    outcome = asyncio.run(VerificationManager(make_settings()).verify(make_source()))

    assert outcome.result == "disabled"
    assert "E_BUDGET" in outcome.blocking_reason
    assert outcome.requests_consumed == 3
    assert outcome.request_budget == 10
    # Everything learned before exhaustion is still on the record.
    assert outcome.robots_allowed is True
    assert outcome.http_status == 200
    assert outcome.identity_ok is True
    joined = outcome.evidence_text()
    assert "robots OK" in joined
    assert "homepage OK (200)" in joined


def test_budget_exhaustion_listing_check_concludes_verified(monkeypatch) -> None:
    """Approved D9 amendment: after exhaustion the zero-request listing
    check may still verify a homepage that was fetched within budget."""
    stub = StubFetcher({
        ROBOTS_URL: make_result(ROBOTS_URL, ROBOTS_ALLOW,
                                content_type="text/plain"),
        BASE: make_result(BASE, LISTING_HOMEPAGE),
        BASE + "rss.xml": BudgetExhausted(consumed=3, cap=10),
    })
    patch_fetcher(monkeypatch, stub)

    outcome = asyncio.run(VerificationManager(make_settings()).verify(make_source()))

    assert outcome.result == "verified"
    assert outcome.method is VerificationMethod.LISTING
    assert outcome.collection_method is CollectionMethod.HTML
    assert outcome.listing_found is True
    assert outcome.requests_consumed == 3  # nothing was fetched beyond budget
    assert outcome.robots_allowed is True
    assert outcome.http_status == 200
    assert outcome.identity_ok is True
    joined = outcome.evidence_text()
    assert "budget exhausted after 3/10" in joined
    assert "zero-request listing check OK" in joined
    assert "no additional requests issued" in joined
    # Only robots + homepage were requested; the probe never left the stub.
    assert stub.requested == [ROBOTS_URL, BASE, BASE + "rss.xml"]


def test_budget_exhaustion_weak_listing_stays_disabled(monkeypatch) -> None:
    """Without a listing, exhaustion still concludes disabled - and the
    zero-request attempt is on the record."""
    stub = StubFetcher({
        ROBOTS_URL: make_result(ROBOTS_URL, ROBOTS_ALLOW,
                                content_type="text/plain"),
        BASE: make_result(BASE, PLAIN_HOMEPAGE),
        BASE + "rss.xml": BudgetExhausted(consumed=3, cap=10),
    })
    patch_fetcher(monkeypatch, stub)

    outcome = asyncio.run(VerificationManager(make_settings()).verify(make_source()))

    assert outcome.result == "disabled"
    assert "E_BUDGET" in outcome.blocking_reason
    assert "zero-request listing check weak (0 links)" in outcome.evidence_text()


def test_feed_found_verifies_with_sample_entry(monkeypatch) -> None:
    stub = StubFetcher({
        ROBOTS_URL: make_result(ROBOTS_URL, ROBOTS_ALLOW,
                                content_type="text/plain"),
        BASE: make_result(BASE, PLAIN_HOMEPAGE),
        BASE + "rss.xml": make_result(BASE + "rss.xml", RSS_FEED,
                                      content_type="application/rss+xml"),
    })
    patch_fetcher(monkeypatch, stub)

    outcome = asyncio.run(VerificationManager(make_settings()).verify(make_source()))

    assert outcome.result == "verified"
    assert outcome.method is VerificationMethod.RSS
    assert outcome.collection_method is CollectionMethod.RSS
    assert outcome.feed_found is True
    assert outcome.feed_url == BASE + "rss.xml"
    assert outcome.sample_entry == {
        "title": "Notice A",
        "url": "https://example.gov.in/press/notice-a.html",
    }
    assert outcome.robots_allowed is True
    assert outcome.http_status == 200
    assert outcome.identity_ok is True
    assert "feed OK" in outcome.evidence_text()
    # robots + homepage + one feed probe
    assert stub.requested == [ROBOTS_URL, BASE, BASE + "rss.xml"]


def test_listing_homepage_verifies_without_extra_requests(monkeypatch) -> None:
    stub = StubFetcher(script_all_probes_missing())
    patch_fetcher(monkeypatch, stub)

    # Upgrade the homepage to a listing page with >= 3 article links.
    stub.script[BASE] = make_result(BASE, LISTING_HOMEPAGE)

    outcome = asyncio.run(VerificationManager(make_settings()).verify(make_source()))

    assert outcome.result == "verified"
    assert outcome.method is VerificationMethod.LISTING
    assert outcome.collection_method is CollectionMethod.HTML
    assert outcome.listing_found is True
    assert outcome.feed_found is None  # no working feed was found
    # Only robots + homepage were fetched; probes hit 404s.
    assert stub.requested[:2] == [ROBOTS_URL, BASE]


def test_no_collectable_content_rejects_with_notes(monkeypatch) -> None:
    stub = StubFetcher(script_all_probes_missing())
    patch_fetcher(monkeypatch, stub)

    outcome = asyncio.run(VerificationManager(make_settings()).verify(make_source()))

    assert outcome.result == "rejected"
    assert outcome.blocking_reason == "no collectable content found within budget"
    assert outcome.robots_allowed is True
    assert outcome.http_status == 200
    assert outcome.identity_ok is True
    joined = outcome.evidence_text()
    for path in ("/rss.xml", "/feed", "/rss", "/atom.xml", "/sitemap.xml"):
        assert path in joined  # every probe attempt is on the record
    assert "listing weak (0 links)" in joined


def test_robots_disallow_rejects_before_any_content_fetch(monkeypatch) -> None:
    stub = StubFetcher({
        ROBOTS_URL: make_result(ROBOTS_URL, ROBOTS_DISALLOW,
                                content_type="text/plain"),
    })
    patch_fetcher(monkeypatch, stub)

    outcome = asyncio.run(VerificationManager(make_settings()).verify(make_source()))

    assert outcome.result == "rejected"
    assert "E_POLICY" in outcome.blocking_reason
    assert outcome.robots_allowed is False
    assert stub.requested == [ROBOTS_URL]  # nothing else was requested
