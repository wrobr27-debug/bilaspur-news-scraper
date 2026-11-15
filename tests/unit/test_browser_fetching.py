"""Browser adapter tests against a localhost JS-rendered site.

Proves the whole point of the browser path:
- a plain HTTP fetch of the demo homepage finds **no** article links
  (the list is injected by JavaScript);
- the Chromium adapter sees the injected listing and the injected bodies;
- the full collection pipeline stores the articles when the source carries
  the operator's browser approval (D10), and stores nothing on a dry-run.

Fully offline: the only server is a local one started by the test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bilaspur_scraper.config.settings import Settings
from bilaspur_scraper.database.models import IngestionRun, Source
from bilaspur_scraper.database.repositories import ArticleRepository, \
    SourceRepository
from bilaspur_scraper.database.session import create_db_engine, create_schema, \
    ensure_schema_columns, make_session_factory
from bilaspur_scraper.discovery.engine import extract_listing_links
from bilaspur_scraper.fetching.browser import BrowserFetcher
from bilaspur_scraper.fetching.fetcher import FetchManager
from bilaspur_scraper.pipeline.run import run_collection
from bilaspur_scraper.schemas.enums import LifecycleState
from js_demo_site import start_server


def _playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _playwright_available(),
    reason="playwright not installed (pip install playwright && "
           "playwright install chromium)")


@pytest.fixture
def demo_site():
    base, server = start_server()
    yield base
    server.shutdown()


def _settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///:memory:"),
        user_agent="bilaspur-scraper-test/0.1 (+mailto:test@example.org)",
        contact_email="test@example.org", request_timeout_s=20,
        max_concurrency=1, per_domain_delay_s=0.0, max_retries=1,
        backoff_base_s=0.0, verification_request_budget=10,
        browser_crawling_enabled=True, respect_robots=True,
        dry_run=False, log_level="WARNING",
    )


async def _http_fetch(settings, url):
    fetcher = FetchManager(settings)
    try:
        return await fetcher.fetch(url)
    finally:
        await fetcher.aclose()


async def _browser_fetch(settings, url):
    fetcher = BrowserFetcher(settings)
    try:
        return await fetcher.fetch(url)
    finally:
        await fetcher.aclose()


def test_http_fetch_finds_no_links_but_browser_does(demo_site):
    """The demo listing exists only after JavaScript runs."""
    settings = _settings()

    http_result = asyncio.run(_http_fetch(settings, demo_site + "/"))
    assert http_result.ok
    # The inline script mentions the class name, but no anchor is rendered.
    assert '<a class="js-story"' not in http_result.text()
    assert extract_listing_links(http_result.text(), demo_site + "/",
                                 max_items=20) == []

    rendered = asyncio.run(_browser_fetch(settings, demo_site + "/"))
    assert rendered.ok
    assert rendered.used_browser is True
    assert '<a class="js-story"' in rendered.text()
    links = extract_listing_links(rendered.text(), demo_site + "/", max_items=20)
    assert len(links) == 3
    assert all("/news/" in link for link in links)


def test_browser_renders_article_body(demo_site):
    """Article text is injected by JS too - only the browser sees it."""
    settings = _settings()
    page = asyncio.run(_browser_fetch(settings,
                                      demo_site + "/news/secl-bilaspur-recruitment"))
    assert page.ok
    text = page.text()
    assert "SECL" in text
    assert "भर्ती" in text
    assert len(text) > 300


def _seed_source(tmp_path, monkeypatch, base, *, browser_approved: bool):
    db_path = tmp_path / "browser.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_db_engine(f"sqlite:///{db_path}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Source(
            source_id="js-demo-site",
            source_name="JS Demo Site",
            source_type="news_portal",
            url=f"{base}/",
            url_kind="homepage",
            coverage_scope="district",
            priority="P1",
            collection_method="html",
            source_verification_status="verified_official",
            lifecycle_state=LifecycleState.ENABLED.value,
            enabled=True,
            collection_endpoints=None,
            browser_approved=browser_approved,
            browser_approved_by="test" if browser_approved else None,
            origin_sheet="test", origin_row=0,
        ))
        session.commit()
    return factory


def test_pipeline_collects_js_rendered_site_with_browser(tmp_path, monkeypatch,
                                                        demo_site):
    factory = _seed_source(tmp_path, monkeypatch, demo_site,
                            browser_approved=True)
    settings = _settings()
    with factory() as session:
        row = SourceRepository(session).require("js-demo-site")
    report = run_collection(settings, row, dry_run=False, max_items=5)
    assert report.ok, report.error_message
    assert report.totals.written == 3
    with factory() as session:
        rows = ArticleRepository(session).recent("js-demo-site", limit=5)
        assert len(rows) == 3
        titles = {r.title for r in rows}
        assert any("फ्लाईओवर" in (t or "") for t in titles)
        for r in rows:
            assert len(r.content_text) > 300
            assert r.bilaspur_relevance in ("high", "medium")


def test_pipeline_without_browser_finds_nothing(tmp_path, monkeypatch, demo_site):
    """Without the operator's browser approval, the same site yields nothing."""
    factory = _seed_source(tmp_path, monkeypatch, demo_site,
                            browser_approved=False)
    settings = _settings()
    with factory() as session:
        row = SourceRepository(session).require("js-demo-site")
    report = run_collection(settings, row, dry_run=False, max_items=5)
    assert report.totals.written == 0
    with factory() as session:
        assert ArticleRepository(session).count_all() == 0


def test_dry_run_on_js_site_writes_nothing(tmp_path, monkeypatch, demo_site):
    factory = _seed_source(tmp_path, monkeypatch, demo_site,
                            browser_approved=True)
    settings = _settings()
    with factory() as session:
        row = SourceRepository(session).require("js-demo-site")
    report = run_collection(settings, row, dry_run=True, max_items=5)
    assert report.ok, report.error_message
    assert report.totals.extracted == 3
    assert report.totals.written == 0
    with factory() as session:
        assert ArticleRepository(session).count_all() == 0
        assert session.query(IngestionRun).count() == 0


def test_additive_migration_adds_new_columns(tmp_path):
    """An older database file gains new model columns automatically."""
    db_path = tmp_path / "old.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            'CREATE TABLE sources (source_id VARCHAR(128) PRIMARY KEY, '
            'source_name TEXT NOT NULL, source_type VARCHAR(64) NOT NULL, '
            'platform VARCHAR(32), url TEXT NOT NULL, url_kind VARCHAR(32) '
            'NOT NULL, coverage_scope VARCHAR(32) NOT NULL, priority VARCHAR(8) '
            'NOT NULL, collection_method VARCHAR(32), source_verification_status '
            'VARCHAR(32), activity_status VARCHAR(48), lifecycle_state VARCHAR(32) '
            'NOT NULL, enabled BOOLEAN, verification_method VARCHAR(32), '
            'verification_evidence TEXT, verification_checked_at DATETIME, '
            'verified_by VARCHAR(128), notes TEXT, origin_sheet VARCHAR(64) '
            'NOT NULL, origin_row INTEGER NOT NULL, first_seen_at DATETIME '
            'NOT NULL, last_checked_at DATETIME)'
        )
    applied = ensure_schema_columns(engine)
    assert any("sources.browser_approved" in item for item in applied)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql(
            "PRAGMA table_info(sources)")}
    assert "browser_approved" in cols
    assert "collection_endpoints" in cols
