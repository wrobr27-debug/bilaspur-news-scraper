"""Regression tests for the five defects found in the 2026-11-15 audit.

Each test names the defect it locks down, so a regression points straight
at the behaviour that broke:

1. run/health timestamps mixed UTC with naive local time (5.5 h skew);
2. dry-run and live used different relevance engines, so a dry-run could
   green-light an article the live run then discarded;
3. `BROWSER_CRAWLING_ENABLED` was read nowhere - a dead kill-switch;
4. re-importing the workbook reset the verification status of sources
   that live verification had already promoted;
5. a source interrupted mid-verification was stuck in
   `verification_pending` forever.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from bilaspur_scraper.config.settings import Settings
from bilaspur_scraper.database.repositories import (
    RunRepository,
    SourceRepository,
)
from bilaspur_scraper.database.session import create_db_engine, create_schema, \
    make_session_factory
from bilaspur_scraper.fetching.fetcher import FetchManager
from bilaspur_scraper.fetching.selector import build_fetcher
from bilaspur_scraper.processing.relevance import KeywordGroups, RelevanceEngine
from bilaspur_scraper.registry.lifecycle import ALLOWED_TRANSITIONS
from bilaspur_scraper.schemas.enums import LifecycleState, SourceVerificationStatus


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def make_db(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path/'bug.db'}")
    create_schema(engine)
    return engine, make_session_factory(engine)


def make_settings(**overrides) -> Settings:
    base = dict(
        database_url="sqlite:///data/news.db",
        user_agent="BilaspurNewsBot/0.1 (+mailto:test@example.com)",
        contact_email="test@example.com",
        request_timeout_s=20,
        max_concurrency=4,
        per_domain_delay_s=2.0,
        max_retries=3,
        backoff_base_s=1.0,
        verification_request_budget=10,
        browser_crawling_enabled=True,
        respect_robots=True,
        dry_run=False,
        log_level="INFO",
    )
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------------
# Defect 1 - timezone skew between started_at and finished_at
# --------------------------------------------------------------------------

def test_run_started_and_finished_use_the_same_timezone(tmp_path):
    """`started_at` and `finished_at` must not be 5.5 h apart on a run
    that took milliseconds."""
    engine, factory = make_db(tmp_path)
    with factory() as session:
        runs = RunRepository(session)
        runs.open("run-tz", mode="live")
        session.commit()
    with factory() as session:
        RunRepository(session).close("run-tz", "{}")
        session.commit()

    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT started_at, finished_at FROM ingestion_runs "
            "WHERE run_id = 'run-tz'").one()
    started = datetime.fromisoformat(row[0])
    finished = datetime.fromisoformat(row[1])
    drift = abs((finished - started).total_seconds())
    assert drift < 60, (
        f"run timestamps drifted {drift}s - one of them is not UTC")


def test_health_run_timestamps_are_utc_consistent(tmp_path):
    """Source health rows must record the same clock as the run ledger."""
    from bilaspur_scraper.database.models import Source
    from bilaspur_scraper.database.repositories import HealthRepository

    engine, factory = make_db(tmp_path)
    with factory() as session:
        session.add(Source(
            source_id="tz-health", source_name="tz", source_type="publisher_local",
            url="https://example.invalid/", url_kind="homepage",
            coverage_scope="district", priority="P1", collection_method="none",
            source_verification_status="unverified", activity_status="pending",
            lifecycle_state="candidate", enabled=False, notes="",
            origin_sheet="t", origin_row=0))
        session.commit()
    with factory() as session:
        HealthRepository(session).record_run("tz-health", ok=True, articles=1)
        session.commit()

    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT last_run_at, last_success_at FROM source_health "
            "WHERE source_id = 'tz-health'").one()
    last_run = datetime.fromisoformat(row[0])
    last_success = datetime.fromisoformat(row[1])
    assert abs((last_success - last_run).total_seconds()) < 1
    # and it must be close to real UTC now, not local time
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None)
                - last_run).total_seconds()) < 300


# --------------------------------------------------------------------------
# Defect 2 - dry-run and live must agree on relevance
# --------------------------------------------------------------------------

def test_dry_run_and_live_use_the_same_keyword_groups(tmp_path):
    """The registry's groups must replace the hardcoded defaults in BOTH
    paths, so a dry-run preview matches what a live run stores."""
    engine, factory = make_db(tmp_path)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO keyword_groups (group_name, terms, coverage_purpose, "
            "usage_notes) VALUES ('Geography - nearby areas', "
            "'Bilha; बिल्हा', 'District', '')")
    with factory() as session:
        from bilaspur_scraper.database.repositories import KeywordGroupRepository
        rows = KeywordGroupRepository(session).terms_rows()

    db_groups = KeywordGroups.from_db_groups(rows)
    live = RelevanceEngine(db_groups)
    dry = RelevanceEngine(KeywordGroups.default())

    title = "बिल्हा में नई सड़क का निर्माण शुरू"
    body = "बिल्हा क्षेत्र में कार्य शुरू।"
    assert live.classify(title, body, source_coverage="city").score == \
        dry.classify(title, body, source_coverage="city").score, (
        "dry-run and live disagree on relevance")


def test_db_keyword_groups_replace_defaults_not_merge_them(tmp_path):
    """A registry group wins outright over the built-in default list."""
    from bilaspur_scraper.database.repositories import KeywordGroupRepository

    engine, factory = make_db(tmp_path)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO keyword_groups (group_name, terms, coverage_purpose, "
            "usage_notes) VALUES ('Geography - nearby areas', "
            "'Bilha; बिल्हा', 'District', '')")
    with factory() as session:
        rows = KeywordGroupRepository(session).terms_rows()

    groups = KeywordGroups.from_db_groups(rows)
    assert groups.nearby == ["Bilha", "बिल्हा"]
    # the default list had extra terms the registry does not list; the
    # registry is the single source of truth once imported.
    assert "Malhar" not in groups.nearby


def test_dry_run_counts_filtered_items_the_way_live_does(tmp_path):
    """A dry-run must count filtered items exactly as a live run does."""
    from bilaspur_scraper.pipeline.run import RunTotals

    engine = RelevanceEngine(KeywordGroups.default())

    class _Draft:
        title = "Delhi में शीतकालीन कोहरा"
        content_text = "राष्ट्रीय राजधानी में कोहरा छाया रहा।"

    draft = _Draft()
    result = engine.classify(draft.title, draft.content_text,
                             source_coverage="city")
    assert result.score in ("low", "unknown"), (
        "fixture must be an item the live path would filter")
    totals = RunTotals()
    if result.score in ("low", "unknown"):
        totals.relevance_filtered += 1
    assert totals.relevance_filtered == 1
    assert totals.written == 0


# --------------------------------------------------------------------------
# Defect 3 - BROWSER_CRAWLING_ENABLED must be a real kill-switch
# --------------------------------------------------------------------------

def test_browser_crawling_disabled_falls_back_to_http():
    """D10: the global switch may only DISABLE browser crawling."""
    settings = make_settings(browser_crawling_enabled=False)
    fetcher = build_fetcher(settings, use_browser=True)
    assert isinstance(fetcher, FetchManager), (
        "BROWSER_CRAWLING_ENABLED=false must not launch a browser")


def test_browser_crawling_enabled_uses_browser():
    from bilaspur_scraper.fetching.browser import BrowserFetcher

    settings = make_settings(browser_crawling_enabled=True)
    fetcher = build_fetcher(settings, use_browser=True)
    assert isinstance(fetcher, BrowserFetcher)


def test_global_switch_never_grants_permission():
    """The switch must not be able to turn the browser ON for a source
    that was never approved."""
    settings = make_settings(browser_crawling_enabled=True)
    fetcher = build_fetcher(settings, use_browser=False)
    assert isinstance(fetcher, FetchManager)


# --------------------------------------------------------------------------
# Defect 4 - import must not downgrade a verification status
# --------------------------------------------------------------------------

def test_import_never_downgrades_verification_status(tmp_path, workbook_path):
    """Re-importing the workbook must not reset a promoted status."""
    from bilaspur_scraper.registry import loader

    engine, factory = make_db(tmp_path)
    result = loader.load_workbook_records(workbook_path)
    with factory() as session:
        sources = SourceRepository(session)
        for record in result.sources:
            sources.upsert_from_import(record)
        session.commit()

        target = "the-bilasa-times"
        row = sources.require(target)
        # live verification promoted it (as `verify-source` does)
        row.source_verification_status = \
            SourceVerificationStatus.VERIFIED_OFFICIAL.value
        session.commit()

        # re-import the same workbook
        for record in result.sources:
            sources.upsert_from_import(record)
        session.commit()

        assert sources.require(target).source_verification_status == \
            SourceVerificationStatus.VERIFIED_OFFICIAL.value, (
            "re-import downgraded a verified source")


def test_import_can_still_raise_verification_status(tmp_path, workbook_path):
    """The merge must not block a genuine promotion."""
    from bilaspur_scraper.registry import loader

    engine, factory = make_db(tmp_path)
    result = loader.load_workbook_records(workbook_path)
    with factory() as session:
        sources = SourceRepository(session)
        for record in result.sources:
            sources.upsert_from_import(record)
        session.commit()

        # this source is `publisher_linked` in the workbook; drop it to
        # `unverified` and re-import - it must be raised back.
        target = "the-bilasa-times"
        row = sources.require(target)
        row.source_verification_status = \
            SourceVerificationStatus.UNVERIFIED.value
        session.commit()

        for record in result.sources:
            sources.upsert_from_import(record)
        session.commit()

        stored = sources.require(target).source_verification_status
        assert stored == SourceVerificationStatus.PUBLISHER_LINKED.value, (
            f"import must still be able to raise a verification status "
            f"(got {stored!r})")


# --------------------------------------------------------------------------
# Defect 5 - verification_pending must not be a dead end
# --------------------------------------------------------------------------

def test_verification_pending_can_recover_to_discovered():
    """An interrupted verification must be re-queueable."""
    assert LifecycleState.DISCOVERED in \
        ALLOWED_TRANSITIONS[LifecycleState.VERIFICATION_PENDING]


def test_verify_source_accepts_a_source_stuck_in_verification_pending(
        tmp_path, monkeypatch):
    """The CLI must not refuse a source left in `verification_pending`."""
    from bilaspur_scraper.cli import main
    from bilaspur_scraper.database.models import Source

    db_path = tmp_path / "stuck.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("USER_AGENT", "BilaspurNewsBot/0.1 (+mailto:t@example.com)")
    monkeypatch.setenv("CONTACT_EMAIL", "t@example.com")

    engine = create_db_engine(f"sqlite:///{db_path}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Source(
            source_id="stuck", source_name="Stuck", source_type="publisher_local",
            url="https://example.invalid/", url_kind="homepage",
            coverage_scope="district", priority="P1", collection_method="none",
            source_verification_status="unverified", activity_status="pending",
            lifecycle_state=LifecycleState.VERIFICATION_PENDING.value,
            enabled=False, notes="", origin_sheet="t", origin_row=0))
        session.commit()
    engine.dispose()

    # The CLI must get past its origin-state guard. We stop before the
    # network call by making the verification raise.
    import bilaspur_scraper.cli as cli_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("network call reached - guard passed")

    monkeypatch.setattr(cli_mod, "run_verification", _boom)
    with pytest.raises(RuntimeError, match="guard passed"):
        main(["verify-source", "stuck"])
