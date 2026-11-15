"""ArticleRepository upsert + HealthRepository (throwaway SQLite, offline)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from bilaspur_scraper.database.models import ArticleRevision, Source
from bilaspur_scraper.database.repositories import ArticleRepository, \
    HealthRepository, SourceRepository
from bilaspur_scraper.database.session import create_db_engine, create_schema, \
    make_session_factory
from bilaspur_scraper.schemas.article import ArticleDraft, ArticleRecord


@pytest.fixture
def session_factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path}/articles.db")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Source(
            source_id="test-source", source_name="Test",
            source_type="district_administration", url="https://example.org",
            url_kind="homepage", coverage_scope="district",
            priority="P1", lifecycle_state="enabled", enabled=True,
            origin_sheet="s", origin_row=1,
        ))
        session.commit()
    return factory


def _record(source_id="test-source", title="Bilaspur drive begins",
            body="A sufficiently long body. " * 15, pub_date=None) -> ArticleRecord:
    draft = ArticleDraft(
        source_id=source_id,
        original_url="https://example.org/news/story-1",
        canonical_url="https://example.org/news/story-1",
        title=title,
        content_text=body,
        language="en",
        published_date=pub_date,
        published_time_precision="date-only" if pub_date else "unknown",
    )
    now = datetime.now(timezone.utc)
    return ArticleRecord(
        draft=draft, bilaspur_relevance="high", relevance_reason="test",
        matched_terms=["Bilaspur"], sensitive=False,
        content_hash="sha256:" + "0" * 64, record_status="collected",
        source_verification_status="verified_official",
        retrieved_at=now, first_seen_at=now, last_seen_at=now,
    )


def test_upsert_inserts_with_revision_one(session_factory):
    with session_factory() as session:
        repo = ArticleRepository(session)
        action, row = repo.upsert(_record(), run_id="run-1")
        session.commit()
        assert action == "inserted"
        assert row.title == "Bilaspur drive begins"
        revs = session.query(ArticleRevision).all()
        assert len(revs) == 1
        assert revs[0].revision == 1
        assert revs[0].content_text == _record().draft.content_text


def test_upsert_same_canonical_is_updated(session_factory):
    with session_factory() as session:
        repo = ArticleRepository(session)
        repo.upsert(_record())
        action, _ = repo.upsert(_record())
        session.commit()
        assert action == "updated"
        assert repo.count_by_source("test-source") == 1


def test_recollection_with_changed_text_adds_revision(session_factory):
    with session_factory() as session:
        repo = ArticleRepository(session)
        repo.upsert(_record())
        session.commit()
        changed = _record(body="Completely different body text now. " * 12)
        action, _ = repo.upsert(changed)
        session.commit()
        assert action == "updated"
        revs = session.query(ArticleRevision).order_by(
            ArticleRevision.revision).all()
        assert [r.revision for r in revs] == [1, 2]
        assert revs[1].content_text == changed.draft.content_text


def test_content_hash_is_signal_not_unique(session_factory):
    with session_factory() as session:
        repo = ArticleRepository(session)
        same_hash_record = _record()
        repo.upsert(same_hash_record)
        draft2 = same_hash_record.draft.model_copy(update={
            "original_url": "https://example.org/news/story-2",
            "canonical_url": "https://example.org/news/story-2"})
        record2 = same_hash_record.model_copy(update={"draft": draft2})
        action2, _ = repo.upsert(record2)  # same hash, different canonical URL
        session.commit()
        assert action2 == "inserted"
        assert repo.count_by_source("test-source") == 2


def test_health_repository_tracks_failures(session_factory):
    with session_factory() as session:
        health = HealthRepository(session)
        health.record_run("test-source", ok=False, articles=0,
                          error_category="E_CONNECT", error_message="down")
        row = health.record_run("test-source", ok=True, articles=3)
        session.commit()
        assert row.consecutive_failures == 0
        assert row.articles_last_run == 3
        assert row.articles_total == 3
        assert row.last_error_category is None
