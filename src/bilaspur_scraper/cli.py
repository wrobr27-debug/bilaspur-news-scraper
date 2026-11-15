"""CLI - Phases 0-1 (`PLAN.md` §6, §14).

Commands:
  init-db           create the SQLite schema
  import-registry   workbook -> sources/aliases/keywords/backlog/queue
  show-sources      registry summary (per-state counts, per-source rows)
  verify-source     networked verification engine (budgeted, evidence-first;
                    authorized from candidate/discovered/rejected/disabled)
  enable-source     operator approval: verified -> enabled (--by required)
  run               collection for one enabled source (feed-only, D11);
                    --dry-run executes everything but writes nothing (D5)
  health            registry health summary

Dry-run, registry import, and CLI scaffolding make no writes to article
tables; only `run` (live) writes articles. Contact configuration gates
every networked command (`ARCHITECTURE.md` §6.1).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Sequence

from .config.logging_config import configure_logging
from .config.settings import load_settings, validate_for_live_crawl
from .database.repositories import (
    AliasRepository,
    BacklogRepository,
    KeywordGroupRepository,
    RunRepository,
    SourceRepository,
    VerificationQueueRepository,
    VerificationRepository,
)
from .database.session import create_db_engine, create_schema, make_session_factory
from .errors import ScraperError
from .pipeline.run import run_collection
from .registry import loader
from .schemas.enums import LifecycleState
from .schemas.source import SourceRecord
from .verification import run_verification

logger = logging.getLogger(__name__)


def _open_session(args: argparse.Namespace):
    settings = load_settings()
    engine = create_db_engine(settings.database_url)
    # Idempotent: creates missing tables AND adds any new columns to an
    # older database file (additive migration, `ensure_schema_columns`).
    create_schema(engine)
    factory = make_session_factory(engine)
    return engine, factory


def cmd_init_db(_: argparse.Namespace) -> int:
    settings = load_settings()
    engine = create_db_engine(settings.database_url)
    create_schema(engine)
    print(f"schema created at {settings.database_url}")
    return 0


def cmd_import_registry(args: argparse.Namespace) -> int:
    result = loader.load_workbook_records(args.workbook)
    engine, factory = _open_session(args)
    create_schema(engine)
    with factory() as session:
        sources = SourceRepository(session)
        inserted = updated = 0
        for record in result.sources:
            outcome = sources.upsert_from_import(record)
            if outcome == "inserted":
                inserted += 1
            else:
                updated += 1
        aliases = AliasRepository(session)
        alias_count = sum(aliases.upsert(a) for a in result.aliases)
        keywords = KeywordGroupRepository(session)
        keyword_count = sum(keywords.upsert(k) for k in result.keywords)
        backlog = BacklogRepository(session)
        backlog_count = sum(backlog.upsert(b) for b in result.backlog)
        queue = VerificationQueueRepository(session)
        queue_count = queue.replace_all(result.queue)
        session.commit()

        state_counts = sources.count_by_state()
        enabled = sources.enabled_count()

    report = loader.ImportReport(
        sources_imported=inserted,
        sources_updated=updated,
        aliases=alias_count,
        keyword_groups=keyword_count,
        backlog_items=backlog_count,
        verification_queue_items=queue_count,
        quarantined=result.quarantined,
        lifecycle_counts=state_counts,
        enabled_count=enabled,
    )

    print(f"sources: {inserted} inserted, {updated} refreshed")
    print(f"aliases: {alias_count} new | keyword groups: {keyword_count} "
          f"| backlog: {backlog_count} | verification queue: {queue_count}")
    print("lifecycle counts: " + ", ".join(
        f"{state}={count}" for state, count in sorted(state_counts.items())
    ))
    if result.quarantined:
        print(f"quarantined rows: {len(result.quarantined)}")
        for row in result.quarantined:
            print(f"  - {row.origin_sheet} row {row.origin_row}: {row.reason} ({row.raw_name})")
    print(f"enabled sources: {enabled} (must always be 0 after import)")
    if enabled:
        print("ERROR: import must never enable a source", file=sys.stderr)
        return 1
    return 0


def cmd_show_sources(args: argparse.Namespace) -> int:
    engine, factory = _open_session(args)
    with factory() as session:
        sources = SourceRepository(session)
        all_sources = sources.all_sources()
        if args.state:
            wanted = args.state.split(",")
            all_sources = [s for s in all_sources if s.lifecycle_state in wanted]
        for s in all_sources:
            print(f"{s.source_id:44} {s.lifecycle_state:20} "
                  f"{s.url_kind:13} {s.priority:2} {s.source_name}")
        print(f"\ntotal: {len(all_sources)} | by state: {sources.count_by_state()} "
              f"| enabled: {sources.enabled_count()}")
    return 0


def cmd_verify_source(args: argparse.Namespace) -> int:
    """Authorize + execute networked verification for one source."""
    settings = load_settings()
    engine, factory = _open_session(args)
    with factory() as session:
        sources = SourceRepository(session)
        source = sources.require(args.source_id)
        current = LifecycleState(source.lifecycle_state)
        allowed_origins = {LifecycleState.CANDIDATE, LifecycleState.DISCOVERED,
                           LifecycleState.DISABLED, LifecycleState.REJECTED,
                           # A verification interrupted mid-flight (Ctrl-C, a
                           # dropped connection) leaves the source in
                           # `verification_pending`. Re-running it must be
                           # possible, otherwise the source is stuck forever.
                           LifecycleState.VERIFICATION_PENDING}
        if current not in allowed_origins:
            print(f"source {args.source_id!r} is {current.value}; verification can be "
                  f"authorized from {sorted(o.value for o in allowed_origins)}",
                  file=sys.stderr)
            return 1
        if current is LifecycleState.REJECTED and not args.reason:
            print("reopening a rejected source requires --reason (audit trail)",
                  file=sys.stderr)
            return 1
        if args.browser and not (args.by or "").strip():
            print("browser verification requires --by <operator name> "
                  "(per-source browser approval, D10)",
                  file=sys.stderr)
            return 1
        if current is LifecycleState.CANDIDATE:
            sources.set_state(args.source_id, LifecycleState.DISCOVERED)
            current = LifecycleState.DISCOVERED
        if current is LifecycleState.REJECTED:
            sources.set_state(args.source_id, LifecycleState.DISCOVERED, reason=args.reason)
        if current is LifecycleState.VERIFICATION_PENDING:
            # Recover an interrupted verification: drop back to `discovered`
            # so the pending -> pending transition below is legal and the
            # new attempt starts from a clean, auditable state.
            sources.set_state(args.source_id, LifecycleState.DISCOVERED)
            current = LifecycleState.DISCOVERED
        sources.set_state(args.source_id, LifecycleState.VERIFICATION_PENDING)
        if args.browser:
            # D10: record the operator's per-source browser approval.
            sources.set_browser_approval(args.source_id, True,
                                         by=args.by.strip())
        session.commit()

        record = SourceRecord.from_row(source)

        outcome = run_verification(settings, record, use_browser=args.browser)

        verifications = VerificationRepository(session)
        verifications.record(
            args.source_id,
            result=outcome.result,
            reviewer="verification-engine",
            method=outcome.method.value if outcome.method else None,
            http_status=outcome.http_status,
            robots_allowed=outcome.robots_allowed,
            feed_found=outcome.feed_found,
            sitemap_found=outcome.sitemap_found,
            listing_found=outcome.listing_found,
            evidence=outcome.evidence_text(),
            blocking_reason=outcome.blocking_reason or None,
            requests_consumed=outcome.requests_consumed,
            request_budget=outcome.request_budget,
            browser_used=outcome.browser_used,
            verification_policy_version=outcome.verification_policy_version,
        )

        target = {
            "verified": LifecycleState.VERIFIED,
            "rejected": LifecycleState.REJECTED,
            "disabled": LifecycleState.DISABLED,
        }[outcome.result]
        sources.set_state(
            args.source_id, target,
            verification_evidence=outcome.evidence_text() if target is LifecycleState.VERIFIED else None,
            verified_by="verification-engine" if target is LifecycleState.VERIFIED else None,
            verification_method=outcome.method.value if outcome.method else None,
        )
        if target is LifecycleState.VERIFIED:
            endpoints = {}
            if outcome.feed_url:
                endpoints["feed_url"] = outcome.feed_url
            source.collection_endpoints = json.dumps(endpoints) if endpoints else None
            if outcome.collection_method:
                source.collection_method = outcome.collection_method.value
        session.commit()

        print(f"{args.source_id}: verification result = {outcome.result}")
        if outcome.blocking_reason:
            print(f"  blocking reason: {outcome.blocking_reason}")
        print(f"  requests: {outcome.requests_consumed}/{outcome.request_budget} "
              f"(policy {outcome.verification_policy_version}, "
              f"browser_used={outcome.browser_used})")
        print(f"  http={outcome.http_status} robots={outcome.robots_allowed} "
              f"feed={outcome.feed_found} sitemap={outcome.sitemap_found} "
              f"listing={outcome.listing_found} identity_ok={outcome.identity_ok}")
        if outcome.feed_url:
            print(f"  feed: {outcome.feed_url}")
        if outcome.sitemap_url:
            print(f"  sitemap: {outcome.sitemap_url}")
        print(f"  evidence: {outcome.evidence_text()}")
        print(f"  lifecycle: verification_pending -> {target.value}")
    return 0 if outcome.result == "verified" else 1


def cmd_enable_source(args: argparse.Namespace) -> int:
    """Operator approval gate: verified -> enabled (D15 approval trail)."""
    if not args.by or not args.by.strip():
        print("enable-source requires --by <operator name> (audit trail)",
              file=sys.stderr)
        return 1
    engine, factory = _open_session(args)
    with factory() as session:
        sources = SourceRepository(session)
        source = sources.require(args.source_id)
        if source.lifecycle_state != LifecycleState.VERIFIED.value:
            print(f"source {args.source_id!r} is {source.lifecycle_state!r}; "
                  f"only a 'verified' source can be enabled", file=sys.stderr)
            return 1
        if not source.verification_evidence:
            print("refusing: verified source has no recorded evidence",
                  file=sys.stderr)
            return 1
        sources.set_state(args.source_id, LifecycleState.ENABLED,
                          verified_by=args.by.strip(),
                          reason=args.reason)
        session.commit()
        print(f"{args.source_id}: verified -> enabled (by {args.by.strip()})")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Collection for one source. Dry-run writes nothing (D5)."""
    settings = load_settings()
    if not args.dry_run:
        try:
            validate_for_live_crawl(settings)
        except ScraperError as exc:
            print(f"live run refused: {exc.message}", file=sys.stderr)
            return 2
    engine, factory = _open_session(args)
    with factory() as session:
        sources = SourceRepository(session)
        source = sources.require(args.source_id)
        if not args.dry_run and not source.enabled:
            print(f"source {args.source_id!r} is not enabled; live collection "
                  f"runs only for enabled sources (dry-run is unrestricted)",
                  file=sys.stderr)
            return 1
        state = LifecycleState(source.lifecycle_state)
        if state is not LifecycleState.ENABLED and not args.dry_run:
            print(f"source {args.source_id!r} is {state.value}; live collection "
                  f"requires enabled", file=sys.stderr)
            return 1
        if state not in (LifecycleState.ENABLED, LifecycleState.VERIFIED) and args.dry_run:
            print(f"source {args.source_id!r} is {state.value}; dry-run is for "
                  f"verified sources only", file=sys.stderr)
            return 1

    report = run_collection(settings, source, dry_run=args.dry_run,
                            max_items=args.max_items)
    totals = report.totals
    mode = "DRY-RUN (no writes)" if args.dry_run else "LIVE"
    print(f"{args.source_id}: {mode} run -> ok={report.ok}")
    if report.error_message:
        print(f"  error: {report.error_code.value if report.error_code else '?'} "
              f"{report.error_message}")
    print(f"  feed items: {totals.feed_items} | extracted: {totals.extracted} "
          f"| written: {totals.written} | updated: {totals.updated}")
    print(f"  relevance-filtered: {totals.relevance_filtered} "
          f"| duplicates: {totals.duplicates} | failures: {totals.failures}")
    for article in report.articles:
        print(f"    + [{article['relevance']}] {article['title']} "
              f"({article['published_date'] or 'no date'}, {article['language']}, "
              f"{article['content_chars']} chars)")
        print(f"      {article['url']}")
    if totals.errors:
        print("  errors:")
        for err in totals.errors[:10]:
            print(f"    - {err}")
    return 0 if report.ok else 1


def cmd_run_all(args: argparse.Namespace) -> int:
    """Manual sweep over every enabled source, in registry priority order.

    This is a manual command, not a scheduler (PLAN.md: no scheduling in
    v1). Each source runs independently; one failure never stops the rest.
    """
    settings = load_settings()
    if not args.dry_run:
        try:
            validate_for_live_crawl(settings)
        except ScraperError as exc:
            print(f"live sweep refused: {exc.message}", file=sys.stderr)
            return 2
    engine, factory = _open_session(args)
    with factory() as session:
        sources = SourceRepository(session)
        rows = [s for s in sources.all_sources() if s.enabled]
    rows.sort(key=lambda s: (s.priority, s.source_id))
    if not rows:
        print("no enabled sources")
        return 0
    print(f"sweeping {len(rows)} enabled source(s), "
          f"{'DRY-RUN (no writes)' if args.dry_run else 'LIVE'}")
    failures = 0
    for row in rows:
        report = run_collection(settings, row, dry_run=args.dry_run,
                                max_items=args.max_items)
        totals = report.totals
        browser = " [browser]" if getattr(row, "browser_approved", False) else ""
        print(f"  {row.source_id}{browser}: ok={report.ok} "
              f"items={totals.feed_items} written={totals.written} "
              f"updated={totals.updated} filtered={totals.relevance_filtered} "
              f"failures={totals.failures}")
        if not report.ok:
            failures += 1
            print(f"    error: {report.error_code.value if report.error_code else '?'} "
                  f"{report.error_message}")
    print(f"done: {len(rows) - failures} ok, {failures} failed")
    return 0 if failures == 0 else 1


def cmd_health(args: argparse.Namespace) -> int:
    engine, factory = _open_session(args)
    with factory() as session:
        sources = SourceRepository(session)
        runs = RunRepository(session)
        interrupted = runs.mark_interrupted()
        session.commit()
        state_counts = sources.count_by_state()
        print("sources by state: " + ", ".join(
            f"{state}={count}" for state, count in sorted(state_counts.items())
        ))
        print(f"enabled sources: {sources.enabled_count()}")
        print(f"interrupted runs recovered: {interrupted}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bilaspur-scraper",
        description="Bilaspur News Scraper v1 (Phases 0-1: registry + "
                    "verification + collection).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create the SQLite schema")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("import-registry", help="import the source workbook")
    p.add_argument("workbook", help="path to the .xlsx source registry")
    p.set_defaults(func=cmd_import_registry)

    p = sub.add_parser("show-sources", help="list registry rows")
    p.add_argument("--state", help="comma-separated lifecycle states to filter")
    p.set_defaults(func=cmd_show_sources)

    p = sub.add_parser("verify-source",
                       help="networked verification for one source "
                            "(budgeted; evidence recorded; no articles written)")
    p.add_argument("source_id")
    p.add_argument("--reason", help="audit reason (required when reopening a "
                                    "rejected source)")
    p.add_argument("--browser", action="store_true",
                   help="verify with the Chromium adapter (D10); requires --by")
    p.add_argument("--by", help="operator name for the browser approval trail")
    p.set_defaults(func=cmd_verify_source)

    p = sub.add_parser("enable-source",
                       help="operator approval: verified -> enabled")
    p.add_argument("source_id")
    p.add_argument("--by", required=True, help="operator name (audit trail)")
    p.add_argument("--reason", help="optional audit note")
    p.set_defaults(func=cmd_enable_source)

    p = sub.add_parser("run",
                       help="collect from one source (feed-first, D11); "
                            "--dry-run writes nothing (D5)")
    p.add_argument("source_id")
    p.add_argument("--dry-run", action="store_true",
                   help="execute everything, write nothing, log evidence")
    p.add_argument("--max-items", type=int, default=10,
                   help="max items per run (default 10)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("run-all",
                       help="manual sweep over every enabled source "
                            "(priority order; not a scheduler)")
    p.add_argument("--dry-run", action="store_true",
                   help="execute everything, write nothing, log evidence")
    p.add_argument("--max-items", type=int, default=10,
                   help="max items per source (default 10)")
    p.set_defaults(func=cmd_run_all)

    p = sub.add_parser("health", help="registry health summary")
    p.set_defaults(func=cmd_health)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level)
    try:
        return args.func(args)
    except ScraperError as exc:
        logger.error("%s", exc.message, extra={"error_code": exc.code.value})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
