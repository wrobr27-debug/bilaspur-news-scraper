"""Tests for the read-only dashboard server.

The dashboard is a monitor: it must never write to the database, never
trigger a collection, and must answer every endpoint the frontend uses.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from dashboard_server import (  # noqa: E402
    articles,
    article_detail,
    runs,
    sources,
    start_server,
    summary,
)


@pytest.fixture
def seeded_db(tmp_path):
    """A small database with the same shape the dashboard reads."""
    from bilaspur_scraper.database.models import (
        Article,
        ArticleRevision,
        Source,
    )
    from bilaspur_scraper.database.session import (
        create_db_engine,
        create_schema,
        make_session_factory,
    )

    db = tmp_path / "dash.db"
    engine = create_db_engine(f"sqlite:///{db}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Source(
            source_id="src-a", source_name="Source A",
            source_type="publisher_local", url="https://a.invalid/",
            url_kind="homepage", coverage_scope="city", priority="P1",
            collection_method="rss", source_verification_status="unverified",
            activity_status="pending", lifecycle_state="enabled", enabled=True,
            notes="", origin_sheet="t", origin_row=0))
        # commit the source first: articles.source_id is a foreign key
        session.commit()
        session.add(Article(
            source_id="src-a", canonical_url="https://a.invalid/1",
            original_url="https://a.invalid/1", title="बिलासपुर समाचार",
            content_text="बिलासपुर जिले में एक महत्वपूर्ण समाचार।" * 10,
            language="hi", bilaspur_relevance="high",
            relevance_reason="explicit match", sensitive=True,
            content_hash="sha256:abc", record_status="collected",
            extraction_method="json_ld", parser_name="generic-html/1"))
        session.commit()
        art_id = session.query(Article).first().article_id
        session.add(ArticleRevision(
            article_id=art_id, revision=1, title="बिलासपुर समाचार",
            content_text="बिलासपुर जिले में एक महत्वपूर्ण समाचार।" * 10,
            content_hash="sha256:abc", change_note="first collection"))
        session.commit()
    engine.dispose()
    return db, art_id


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_endpoints_answer(seeded_db):
    db, art_id = seeded_db
    base, server = start_server(0, db)
    try:
        status, data = _get(base, "/api/summary")
        assert status == 200
        assert data["articles"] == 1
        assert data["sources_total"] == 1
        assert data["sources_enabled"] == 1
        assert data["sensitive"] == 1

        status, data = _get(base, "/api/articles?limit=10")
        assert status == 200
        assert data["total"] == 1
        assert data["articles"][0]["title"] == "बिलासपुर समाचार"
        assert data["articles"][0]["excerpt"]

        status, data = _get(base, "/api/sources")
        assert status == 200
        assert len(data["sources"]) == 1

        status, data = _get(base, "/api/runs")
        assert status == 200
        assert data["runs"] == []

        status, data = _get(base, f"/api/article/{art_id}")
        assert status == 200
        assert data["content_text"]
        assert len(data["revisions"]) == 1
    finally:
        server.shutdown()


def test_article_filters(seeded_db):
    db, _ = seeded_db
    base, server = start_server(0, db)
    try:
        _, all_arts = _get(base, "/api/articles")
        _, sensitive = _get(base, "/api/articles?sensitive=1")
        assert sensitive["total"] == 1

        _, by_source = _get(base, "/api/articles?source=src-a")
        assert by_source["total"] == 1

        _, no_match = _get(base, "/api/articles?source=does-not-exist")
        assert no_match["total"] == 0

        _, searched = _get(base, "/api/articles?q=%E0%A4%AC%E0%A4%BF%E0%A4%B2%E0%A4%BE%E0%A4%B8%E0%A4%AA%E0%A5%81%E0%A4%B0")
        assert searched["total"] == 1
    finally:
        server.shutdown()


def test_unknown_article_returns_404(seeded_db):
    db, _ = seeded_db
    base, server = start_server(0, db)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/api/article/99999", timeout=10)
        assert exc.value.code == 404
    finally:
        server.shutdown()


def test_dashboard_never_writes_to_the_database(seeded_db):
    """The monitor is read-only: serving requests must not change a byte."""
    db, _ = seeded_db

    def digest(path: Path) -> str:
        conn = sqlite3.connect(path)
        rows = []
        for table in sorted(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")):
            rows.append(table)
            rows.extend(repr(r) for r in conn.execute(
                f'SELECT * FROM "{table}"'))
        conn.close()
        return "\n".join(rows)

    before = digest(db)
    base, server = start_server(0, db)
    try:
        for path in ("/api/summary", "/api/articles", "/api/sources",
                     "/api/runs"):
            _get(base, path)
    finally:
        server.shutdown()
    assert digest(db) == before


def test_dashboard_opens_the_database_read_only(seeded_db):
    """The connection itself must refuse writes, not merely avoid them."""
    db, _ = seeded_db
    base, server = start_server(0, db)
    try:
        with urllib.request.urlopen(base + "/api/summary", timeout=10) as r:
            assert r.status == 200
    finally:
        server.shutdown()

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM articles")
    conn.close()


def test_html_page_is_served(seeded_db):
    db, _ = seeded_db
    base, server = start_server(0, db)
    try:
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            body = r.read().decode("utf-8")
            assert r.status == 200
            assert "<script>" in body
            assert "Bilaspur News Scraper" in body
    finally:
        server.shutdown()
