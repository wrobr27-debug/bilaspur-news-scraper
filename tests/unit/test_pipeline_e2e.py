"""End-to-end pipeline test against a local HTTP server (fully offline).

Proves: enabled-only gating, feed-first collection (D11), extraction,
relevance, dry-run zero-writes (D5), live persistence + run ledger +
health, dedup on re-run.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bilaspur_scraper.config.settings import Settings
from bilaspur_scraper.database.models import IngestionRun, Source
from bilaspur_scraper.database.repositories import ArticleRepository, \
    HealthRepository, RunRepository, SourceRepository
from bilaspur_scraper.database.session import create_db_engine, create_schema, \
    make_session_factory
from bilaspur_scraper.pipeline.run import run_collection
from bilaspur_scraper.schemas.enums import LifecycleState

ROBOTS = b"User-agent: *\nAllow: /\n"

FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test Portal</title>
<item><title>Bilaspur civic drive launched</title>
<link>%(base)s/news/story-1</link>
<pubDate>Mon, 05 Jan 2026 10:00:00 +0000</pubDate>
<description>Drive summary</description></item>
<item><title>SECL reports output rise</title>
<link>%(base)s/news/story-2</link>
<pubDate>Tue, 06 Jan 2026 09:00:00 +0000</pubDate>
</item></channel></rss>"""

PAGE_TEMPLATE = """<html><head>
<title>Page</title>
<script type="application/ld+json">
{{"@type": "NewsArticle", "headline": "{title}",
 "datePublished": "2026-01-05T10:30:00+05:30"}}
</script></head><body><article>
<p>{body}</p><p>Second paragraph with additional district context
and quotes from officials to make the body comfortably longer than the
minimum length gate so extraction succeeds on this fixture.</p>
</article></body></html>"""

PAGES = {
    "/news/story-1": ("Bilaspur civic drive launched",
                      "The Bilaspur municipal corporation in Chhattisgarh "
                      "launched a citywide cleanliness drive this week. "
                      "Officials said all wards will be covered and ward "
                      "committees will monitor progress daily."),
    "/news/story-2": ("SECL reports output rise",
                      "South Eastern Coalfields Limited (SECL) reported a "
                      "rise in coal output. The Bilaspur-headquartered "
                      "company said Chhattisgarh mines contributed most of "
                      "the growth in the quarter."),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, body: bytes, content_type="text/html; charset=utf-8",
              status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/robots.txt":
            self._send(ROBOTS, "text/plain")
        elif self.path == "/rss.xml":
            base = f"http://{self.headers['Host']}"
            self._send((FEED_TEMPLATE % {"base": base}).encode("utf-8"),
                       "application/rss+xml")
        elif self.path in PAGES:
            title, body = PAGES[self.path]
            html = PAGE_TEMPLATE.format(title=title, body=body)
            self._send(html.encode("utf-8"))
        else:
            self._send(b"not found", "text/plain", 404)


@pytest.fixture
def local_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def enabled_source(local_site, tmp_path, monkeypatch):
    db_path = tmp_path / "e2e.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_db_engine(f"sqlite:///{db_path}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Source(
            source_id="local-portal",
            source_name="Local Test Portal",
            source_type="news_portal",
            url=f"{local_site}/",
            url_kind="homepage",
            coverage_scope="district",
            priority="P1",
            collection_method="rss",
            source_verification_status="verified_official",
            lifecycle_state=LifecycleState.ENABLED.value,
            enabled=True,
            collection_endpoints=json.dumps(
                {"feed_url": f"{local_site}/rss.xml"}),
            origin_sheet="test", origin_row=0,
        ))
        session.commit()
    return factory


def _settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///:memory:"),
        user_agent="bilaspur-scraper-test/0.1",
        contact_email="test@example.org", request_timeout_s=10,
        max_concurrency=1, per_domain_delay_s=0.0, max_retries=1,
        backoff_base_s=0.0, verification_request_budget=10,
        browser_crawling_enabled=False, respect_robots=True,
        dry_run=False, log_level="WARNING",
    )


def _source_row(factory, source_id="local-portal"):
    with factory() as session:
        return SourceRepository(session).require(source_id)


def test_dry_run_writes_nothing(local_site, enabled_source):
    settings = _settings()
    report = run_collection(settings, _source_row(enabled_source),
                            dry_run=True, max_items=5)
    assert report.ok
    assert report.totals.extracted == 2
    assert report.totals.written == 0
    # D5: dry-run must write nothing anywhere
    with enabled_source() as session:
        assert ArticleRepository(session).count_all() == 0
        runs = session.query(IngestionRun).count()
        assert runs == 0


def test_live_run_persists_articles(local_site, enabled_source):
    settings = _settings()
    row = _source_row(enabled_source)
    report = run_collection(settings, row, dry_run=False, max_items=5)
    assert report.ok, report.error_message
    assert report.totals.written == 2
    assert report.run_id
    with enabled_source() as session:
        articles = ArticleRepository(session)
        assert articles.count_by_source("local-portal") == 2
        rows = articles.recent("local-portal", limit=5)
        titles = {r.title for r in rows}
        assert "Bilaspur civic drive launched" in titles
        assert "SECL reports output rise" in titles
        for r in rows:
            assert r.bilaspur_relevance == "high"
            assert r.published_time_precision == "timestamp"
            assert r.record_status == "collected"
            assert len(r.content_text) > 300
        # run ledger + health written on live runs
        run = session.get(IngestionRun, report.run_id)
        assert run is not None and run.finished_at is not None
        health = HealthRepository(session).get("local-portal")
        assert health.articles_total == 2
        assert health.consecutive_failures == 0


def test_second_live_run_dedups(local_site, enabled_source):
    settings = _settings()
    row = _source_row(enabled_source)
    run_collection(settings, row, dry_run=False)
    report = run_collection(settings, row, dry_run=False)
    assert report.ok
    assert report.totals.written == 0
    assert report.totals.updated == 2  # same canonical URLs -> refresh only
    with enabled_source() as session:
        assert ArticleRepository(session).count_by_source("local-portal") == 2


# --- D11 fall-through: no feed -> sitemap -> listing ------------------------

LISTING_HOMEPAGE = """<html><head><title>District Portal</title></head><body>
<a href="/press/notice-1.html">Bilaspur notices issued</a>
<a href="/press/notice-2.html">Bilaspur tenders opened</a>
<a href="/press/notice-3.html">Bilaspur adm orders</a>
</body></html>"""

LISTING_PAGES = {
    "/press/notice-1.html": ("Bilaspur district notices issued",
                             "The Bilaspur district administration in "
                             "Chhattisgarh issued public notices today. "
                             "Officials said the orders concern ward works "
                             "and will be displayed at the collectorate "
                             "notice board for the coming fortnight."),
    "/press/notice-2.html": ("Bilaspur municipal tenders opened",
                             "Bilaspur municipal corporation opened new "
                             "tenders for road works across city wards in "
                             "Chhattisgarh. The commissioner said bids close "
                             "this month and awards will be published on the "
                             "official portal."),
    "/press/notice-3.html": ("Bilaspur administrative orders",
                             "The Bilaspur collectorate published "
                             "administrative orders for the district in "
                             "Chhattisgarh, covering transfers and section "
                             "duties. All orders are dated and signed by the "
                             "concerned officials."),
}

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>%(base)s/press/notice-1.html</loc></url>
<url><loc>%(base)s/press/notice-2.html</loc></url>
<url><loc>%(base)s/press/notice-3.html</loc></url>
</urlset>"""


class NoFeedHandler(BaseHTTPRequestHandler):
    """A portal with no feed at all: only listing or sitemap discovery."""

    def log_message(self, *args):  # silence
        pass

    def _send(self, body: bytes, content_type="text/html; charset=utf-8",
              status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/robots.txt":
            self._send(ROBOTS, "text/plain")
        elif self.path == "/":
            self._send(LISTING_HOMEPAGE.encode("utf-8"))
        elif self.path in LISTING_PAGES:
            title, body = LISTING_PAGES[self.path]
            self._send(PAGE_TEMPLATE.format(title=title, body=body).encode("utf-8"))
        else:
            self._send(b"not found", "text/plain", 404)


class SitemapOnlyHandler(NoFeedHandler):
    """Homepage carries no article links; only /sitemap.xml reveals URLs."""

    def do_GET(self):
        if self.path == "/":
            self._send(b"<html><head><title>Portal</title></head>"
                       b"<body><p>plain portal page</p></body></html>"
                       .encode("utf-8"))
        elif self.path == "/sitemap.xml":
            base = f"http://{self.headers['Host']}"
            self._send((SITEMAP % {"base": base}).encode("utf-8"),
                       "application/xml")
        else:
            super().do_GET()


@pytest.fixture
def listing_source(tmp_path, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), NoFeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    yield _seed_source(tmp_path, monkeypatch, base)
    server.shutdown()


@pytest.fixture
def sitemap_source(tmp_path, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), SitemapOnlyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    yield _seed_source(tmp_path, monkeypatch, base)
    server.shutdown()


def _seed_source(tmp_path, monkeypatch, base):
    db_path = tmp_path / "fallback.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_db_engine(f"sqlite:///{db_path}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Source(
            source_id="gov-portal",
            source_name="Government Portal (no feed)",
            source_type="district_government",
            url=f"{base}/",
            url_kind="homepage",
            coverage_scope="district",
            priority="P1",
            collection_method="html",
            source_verification_status="verified_official",
            lifecycle_state=LifecycleState.ENABLED.value,
            enabled=True,
            collection_endpoints=None,  # verification found no feed
            origin_sheet="test", origin_row=0,
        ))
        session.commit()
    return factory


def test_listing_fallback_dry_run_writes_nothing(listing_source):
    settings = _settings()
    report = run_collection(settings, _source_row(listing_source, "gov-portal"),
                            dry_run=True, max_items=5)
    assert report.ok, report.error_message
    assert report.totals.extracted == 3
    assert report.totals.written == 0
    with listing_source() as session:
        assert ArticleRepository(session).count_all() == 0
        assert session.query(IngestionRun).count() == 0


def test_listing_fallback_live_run_persists(listing_source):
    settings = _settings()
    report = run_collection(settings, _source_row(listing_source, "gov-portal"),
                            dry_run=False, max_items=5)
    assert report.ok, report.error_message
    assert report.totals.written == 3
    with listing_source() as session:
        rows = ArticleRepository(session).recent("gov-portal", limit=5)
        assert len(rows) == 3
        for r in rows:
            assert r.bilaspur_relevance == "high"
            assert r.extraction_method in ("json_ld", "html", "trafilatura")


def test_sitemap_fallback_live_run_persists(sitemap_source):
    settings = _settings()
    report = run_collection(settings, _source_row(sitemap_source, "gov-portal"),
                            dry_run=False, max_items=5)
    assert report.ok, report.error_message
    assert report.totals.written == 3
    with sitemap_source() as session:
        rows = ArticleRepository(session).recent("gov-portal", limit=5)
        assert len(rows) == 3
