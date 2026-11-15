from sqlalchemy import select, text

from bilaspur_scraper.database.repositories import (
    AliasRepository,
    BacklogRepository,
    KeywordGroupRepository,
    RunRepository,
    SourceRepository,
    VerificationQueueRepository,
    VerificationRepository,
)
from bilaspur_scraper.database.session import create_db_engine, create_schema, \
    make_session_factory
from bilaspur_scraper.errors import LifecycleError
from bilaspur_scraper.registry import loader
from bilaspur_scraper.schemas.enums import LifecycleState


def make_db(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path/'t.db'}")
    create_schema(engine)
    return engine, make_session_factory(engine)


def import_once(engine, factory, workbook_path):
    result = loader.load_workbook_records(workbook_path)
    with factory() as session:
        sources = SourceRepository(session)
        for record in result.sources:
            sources.upsert_from_import(record)
        aliases = AliasRepository(session)
        for a in result.aliases:
            aliases.upsert(a)
        keywords = KeywordGroupRepository(session)
        for k in result.keywords:
            keywords.upsert(k)
        backlog = BacklogRepository(session)
        for b in result.backlog:
            backlog.upsert(b)
        VerificationQueueRepository(session).replace_all(result.queue)
        session.commit()
    return result


def test_import_is_idempotent_and_never_enables(tmp_path, workbook_path):
    engine, factory = make_db(tmp_path)
    import_once(engine, factory, workbook_path)
    import_once(engine, factory, workbook_path)

    with factory() as session:
        sources = SourceRepository(session)
        assert sources.enabled_count() == 0
        counts = sources.count_by_state()
        assert counts.get(LifecycleState.CANDIDATE.value, 0) > 0
        assert counts.get(LifecycleState.EXCLUDED.value, 0) >= 2
        assert AliasRepository(session).count() >= 3
        assert KeywordGroupRepository(session).count() == 2
        assert BacklogRepository(session).count() == 3
        assert VerificationQueueRepository(session).count() == 1


def test_full_lifecycle_requires_evidence_and_approval(tmp_path, workbook_path):
    engine, factory = make_db(tmp_path)
    import_once(engine, factory, workbook_path)

    with factory() as session:
        sources = SourceRepository(session)
        sid = "district-administration-bilaspur"

        # candidate -> discovered -> verification_pending -> verified
        sources.set_state(sid, LifecycleState.DISCOVERED)
        sources.set_state(sid, LifecycleState.VERIFICATION_PENDING)
        with_no_evidence = False
        try:
            sources.set_state(sid, LifecycleState.VERIFIED)
        except LifecycleError:
            with_no_evidence = True
        assert with_no_evidence, "verifying without evidence must fail"

        sources.set_state(sid, LifecycleState.VERIFIED,
                          verified_by="owner", verification_evidence="evidence-001",
                          verification_method="rss")
        sources.set_state(sid, LifecycleState.ENABLED, verified_by="owner")
        source = sources.require(sid)
        assert source.enabled is True
        assert source.lifecycle_state == LifecycleState.ENABLED.value
        session.commit()

    # Reload and confirm persistence.
    with factory() as session:
        source = SourceRepository(session).require(sid)
        assert source.enabled is True
        assert source.verification_evidence == "evidence-001"


def test_cannot_enable_candidate_directly(tmp_path, workbook_path):
    engine, factory = make_db(tmp_path)
    import_once(engine, factory, workbook_path)
    with factory() as session:
        sources = SourceRepository(session)
        try:
            sources.set_state("the-bilasa-times", LifecycleState.ENABLED,
                              verified_by="owner")
            raised = False
        except LifecycleError:
            raised = True
        assert raised


def test_verification_evidence_records_budget_fields(tmp_path):
    engine, factory = make_db(tmp_path)
    create_schema(engine)
    with factory() as session:
        # A source row to satisfy the FK.
        session.execute(
            text(
                "INSERT INTO sources (source_id, source_name, source_type, platform,"
                " url, url_kind, coverage_scope, priority, collection_method,"
                " source_verification_status, activity_status, lifecycle_state,"
                " enabled, notes, origin_sheet, origin_row, first_seen_at)"
                " VALUES ('s1', 'S1', 'police', 'website', 'https://x/',"
                " 'homepage', 'district', 'P1', 'none', 'unverified', 'pending',"
                " 'candidate', 0, '', 'test', 1, CURRENT_TIMESTAMP)")
        )
        row = VerificationRepository(session).record(
            "s1", result="verified", reviewer="owner", requests_consumed=7,
            request_budget=10, browser_used=False,
            verification_policy_version="v1",
        )
        session.commit()
        assert row.requests_consumed == 7
        assert row.request_budget == 10
        assert row.browser_used is False
        assert row.verification_policy_version == "v1"


def test_dry_run_never_writes_run_row(tmp_path):
    engine, factory = make_db(tmp_path)
    with factory() as session:
        runs = RunRepository(session)
        try:
            runs.open("dry-1", mode="dry")
            raised = False
        except LifecycleError:
            raised = True
        assert raised, "dry-run must not create an ingestion_runs row (D5)"


def test_interrupted_run_recovery(tmp_path):
    engine, factory = make_db(tmp_path)
    with factory() as session:
        runs = RunRepository(session)
        runs.open("run-1")
        # no close -> interrupted on next startup
        session.commit()
    with factory() as session:
        recovered = RunRepository(session).mark_interrupted()
        assert recovered == 1
        from bilaspur_scraper.database.models import IngestionRun
        row = session.scalars(
            select(IngestionRun).where(IngestionRun.run_id == "run-1")
        ).one()
        assert row.finished_at is not None
        assert "interrupted" in row.totals
