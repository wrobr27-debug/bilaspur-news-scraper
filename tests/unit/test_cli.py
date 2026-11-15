"""CLI-level tests using a throwaway SQLite DB (no network, no real registry)."""

from __future__ import annotations

import pytest

from bilaspur_scraper.cli import main
from bilaspur_scraper.database.repositories import SourceRepository
from bilaspur_scraper.database.session import create_db_engine, create_schema, \
    make_session_factory
from bilaspur_scraper.schemas.enums import CollectionMethod, LifecycleState, \
    VerificationMethod
from bilaspur_scraper.verification.manager import VerificationOutcome
from tests.conftest import build_workbook


def _stub_outcome(result: str = "verified") -> VerificationOutcome:
    return VerificationOutcome(
        source_id="x", result=result,
        blocking_reason="" if result == "verified" else "blocked",
        requests_consumed=3, request_budget=10,
        method=VerificationMethod.RSS if result == "verified" else None,
        collection_method=CollectionMethod.RSS if result == "verified" else None,
        http_status=200, robots_allowed=True, feed_found=result == "verified",
        sitemap_found=False, listing_found=False,
        feed_url="https://example.org/rss.xml" if result == "verified" else None,
        identity_ok=True, evidence_notes=["stub"],
    )


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    db_path = tmp_path / "cli.db"
    workbook = build_workbook(tmp_path / "registry.xlsx")
    engine = create_db_engine(f"sqlite:///{db_path}")
    create_schema(engine)
    from bilaspur_scraper.registry import loader
    result = loader.load_workbook_records(workbook)
    factory = make_session_factory(engine)
    with factory() as session:
        sources = SourceRepository(session)
        for record in result.sources:
            sources.upsert_from_import(record)
        session.commit()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    return factory


@pytest.fixture
def stub_verification(monkeypatch):
    """Replace the networked engine - CLI tests never touch the network."""
    holder = {"outcome": _stub_outcome("verified")}

    def _run(settings, source, *, use_browser=False):
        holder["use_browser"] = use_browser
        return holder["outcome"]

    monkeypatch.setattr("bilaspur_scraper.cli.run_verification", _run)
    return holder


def test_verify_source_moves_candidate_to_verified(cli_db, stub_verification):
    code = main(["verify-source", "district-administration-bilaspur"])
    assert code == 0
    with cli_db() as session:
        source = SourceRepository(session).require("district-administration-bilaspur")
        assert source.lifecycle_state == "verified"
        assert source.verification_method == "rss"
        assert source.collection_method == "rss"
        assert "feed_url" in (source.collection_endpoints or "")
        assert source.verification_evidence


def test_verify_source_records_rejection(cli_db, stub_verification):
    stub_verification["outcome"] = _stub_outcome("rejected")
    code = main(["verify-source", "district-administration-bilaspur"])
    assert code == 1
    with cli_db() as session:
        source = SourceRepository(session).require("district-administration-bilaspur")
        assert source.lifecycle_state == "rejected"


def test_verify_source_records_budget_disabled(cli_db, stub_verification):
    stub_verification["outcome"] = VerificationOutcome(
        source_id="x", result="disabled",
        blocking_reason="budget exhausted (E_BUDGET)",
        requests_consumed=10, request_budget=10)
    code = main(["verify-source", "district-administration-bilaspur"])
    assert code == 1
    with cli_db() as session:
        source = SourceRepository(session).require("district-administration-bilaspur")
        # budget exhaustion must land on disabled, never rejected (D9)
        assert source.lifecycle_state == "disabled"


def test_verify_source_rejects_excluded_stub(cli_db, stub_verification):
    code = main(["verify-source", "sindhu-youth-media"])
    assert code == 1
    with cli_db() as session:
        source = SourceRepository(session).require("sindhu-youth-media")
        assert source.lifecycle_state == "excluded"


def test_rejected_reopening_requires_reason(cli_db, stub_verification):
    with cli_db() as session:
        sources = SourceRepository(session)
        sid = "the-bilasa-times"
        sources.set_state(sid, LifecycleState.DISCOVERED)
        sources.set_state(sid, LifecycleState.VERIFICATION_PENDING)
        sources.set_state(sid, LifecycleState.REJECTED)
        session.commit()
    # Without --reason the CLI must refuse (exit 1) - before any network work.
    assert main(["verify-source", sid]) == 1
    # With --reason it reopens, re-verifies (stubbed), and lands on verified.
    assert main(["verify-source", sid, "--reason",
                 "ownership re-confirmed with new evidence"]) == 0
    with cli_db() as session:
        source = SourceRepository(session).require(sid)
        assert source.lifecycle_state == "verified"


def test_enable_source_requires_verified(cli_db, stub_verification):
    code = main(["enable-source", "district-administration-bilaspur",
                 "--by", "operator"])
    assert code == 1  # not verified yet

    main(["verify-source", "district-administration-bilaspur"])
    code = main(["enable-source", "district-administration-bilaspur",
                 "--by", "operator"])
    assert code == 0
    with cli_db() as session:
        source = SourceRepository(session).require("district-administration-bilaspur")
        assert source.lifecycle_state == "enabled"
        assert source.enabled is True
        assert source.verified_by == "operator"
