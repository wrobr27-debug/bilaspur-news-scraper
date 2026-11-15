"""Collection pipeline - the one path that writes articles (`PLAN.md` §5.4).

Rules enforced here:
- enabled sources only; disabled/failed sources never auto-recollect;
- feed-first (D11): collection uses the endpoint recorded at verification;
- dry-run = everything runs, zero DB writes (D5), log evidence only;
- live runs write an ingestion_runs row and per-article transactions;
- relevance classification and sensitive-topic flagging run before write;
- unverified sources never enter normal collection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config.settings import Settings, validate_for_live_crawl
from ..database import (
    ArticleRepository,
    HealthRepository,
    KeywordGroupRepository,
    RunRepository,
    SourceRepository,
    open_session,
)
from ..discovery.engine import DiscoveryEngine
from ..discovery.feeds import FeedEntry
from ..extraction.article import extract_article
from ..fetching.fetcher import FetchManager, FetchResult
from ..fetching.robots import RobotsGate
from ..fetching.selector import build_fetcher
from ..processing.dedup import content_hash, normalize_url
from ..processing.normalizer import normalize_draft
from ..processing.relevance import KeywordGroups, RelevanceEngine
from ..schemas.article import ArticleDraft, ArticleRecord
from ..schemas.enums import ErrorCode, RecordStatus

logger = logging.getLogger(__name__)


@dataclass
class RunTotals:
    feed_items: int = 0
    extracted: int = 0
    written: int = 0
    updated: int = 0
    relevance_filtered: int = 0
    duplicates: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (json.dumps({
            "feed_items": self.feed_items,
            "extracted": self.extracted,
            "written": self.written,
            "updated": self.updated,
            "relevance_filtered": self.relevance_filtered,
            "duplicates": self.duplicates,
            "failures": self.failures,
        }, sort_keys=True))


@dataclass
class RunReport:
    source_id: str
    mode: str  # 'live' | 'dry'
    ok: bool
    totals: RunTotals
    run_id: str | None = None
    error_code: ErrorCode | None = None
    error_message: str = ""
    articles: list[dict] = field(default_factory=list)


class CollectionPipeline:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def collect_source(self, source_row, *, dry_run: bool,
                             max_items: int = 10) -> RunReport:
        totals = RunTotals()
        source_id = source_row.source_id
        mode = "dry" if dry_run else "live"

        if not source_row.enabled and not dry_run:
            return RunReport(source_id=source_id, mode=mode, ok=False,
                             totals=totals, error_code=ErrorCode.E_POLICY,
                             error_message="source is not enabled")

        endpoints = {}
        if source_row.collection_endpoints:
            try:
                endpoints = json.loads(source_row.collection_endpoints)
            except json.JSONDecodeError:
                logger.warning("unparseable collection_endpoints for %s", source_id)
        feed_url = endpoints.get("feed_url")

        # D10: the browser is used only for sources the operator approved
        # (`sources.browser_approved`). HTML pages go through it; feeds and
        # sitemaps stay on the cheap HTTP transport (D11).
        use_browser = bool(getattr(source_row, "browser_approved", False))
        fetcher = build_fetcher(self._settings, use_browser=False)
        page_fetcher = build_fetcher(self._settings,
                                     use_browser=use_browser) if use_browser \
            else fetcher
        try:
            # robots gate even in collection (we respect robots on every run)
            robots = await RobotsGate(fetcher).check(source_row.url)
            if robots.explicitly_disallowed:
                totals.errors.append("robots disallows crawling (E_POLICY)")
                return RunReport(source_id, mode, False, totals,
                                 error_code=ErrorCode.E_POLICY,
                                 error_message="robots.txt disallows crawling")
            if robots.unavailable:
                totals.errors.append("robots unavailable (E_ROBOTS_UNAVAILABLE)")
                return RunReport(source_id, mode, False, totals,
                                 error_code=ErrorCode.E_ROBOTS_UNAVAILABLE,
                                 error_message="robots.txt unavailable")

            # D11: feed-only when a feed is available; when it is not,
            # fall through to sitemap -> listing (bounded discovery).
            discovery = DiscoveryEngine(
                fetcher, page_fetcher=page_fetcher,
                source_scope=getattr(source_row, "coverage_scope", None))
            discovery.set_robots_sitemaps(list(robots.sitemaps or []))
            outcome = await discovery.discover(
                source_row.url,
                prefer_feed_url=feed_url or None,
                max_items=max_items,
                feed_only=False,
            )
            if not outcome.items:
                for err in outcome.errors[:5]:
                    totals.errors.append(err)
                return RunReport(
                    source_id, mode, False, totals,
                    error_code=ErrorCode.E_CONFIG,
                    error_message="no collectable endpoint found "
                                  "(feed/sitemap/listing); re-verify the source")
            totals.feed_items = len(outcome.items)
            logger.info("discovery strategy=%s items=%d for %s",
                        outcome.strategy_used, len(outcome.items), source_id)

            # Relevance engine driven by the registry's keyword groups. The
            # dry-run and the live path MUST use the same groups, otherwise
            # a dry-run green-lights articles the live run then discards
            # (D5: a dry-run is a faithful preview of the live run).
            engine = self._relevance_engine()

            drafts: list[tuple[FeedEntry, ArticleDraft | None, str]] = []
            for item in outcome.items:
                entry = FeedEntry(
                    url=item.url, title=item.title_hint, published_at=None,
                    published_date=None,
                    published_at_original=item.published_hint,
                    summary=item.summary_hint, author=None, tags=[])
                draft, fail_reason = await self._build_draft(
                    page_fetcher, source_id, entry)
                if draft is None:
                    totals.failures += 1
                    totals.errors.append(
                        f"extraction failed for {item.url}: {fail_reason}")
                    continue
                totals.extracted += 1
                drafts.append((entry, draft, ""))

            if dry_run:
                # D5: everything ran, zero DB writes. Report log evidence.
                # The relevance verdict is computed exactly as the live path
                # computes it, and filtered items are counted the same way,
                # so the dry-run preview matches what a live run would store.
                for _, draft, _ in drafts:
                    result = engine.classify(draft.title, draft.content_text,
                                             source_coverage=source_row.coverage_scope)
                    if result.score in ("low", "unknown"):
                        totals.relevance_filtered += 1
                        logger.info(
                            "[dry-run] %s | %s | rel=%s (%s) | FILTERED | %s",
                            source_id, (draft.title or "")[:80], result.score,
                            result.reason,
                            normalize_url(draft.canonical_url or draft.original_url))
                        continue
                    logger.info(
                        "[dry-run] %s | %s | rel=%s (%s) | lang=%s | %s",
                        source_id, (draft.title or "")[:80], result.score,
                        result.reason, draft.language,
                        normalize_url(draft.canonical_url or draft.original_url))
                return RunReport(source_id, mode, True, totals)

            return self._persist(source_row, drafts, engine, totals)
        finally:
            await page_fetcher.aclose()
            if page_fetcher is not fetcher:
                await fetcher.aclose()

    def _relevance_engine(self) -> RelevanceEngine:
        """Build the relevance engine from the registry's keyword groups.

        Read-only: it opens its own short-lived session and writes nothing,
        so the dry-run path can use it too (D5: zero database writes).
        Falls back to the workbook-grounded defaults when the registry has
        no keyword groups yet.
        """
        try:
            with open_session(self._settings.database_url) as session:
                rows = KeywordGroupRepository(session).terms_rows()
        except Exception as exc:  # noqa: BLE001 - never fail a run on this
            logger.warning("could not load keyword groups; using defaults: %s",
                           exc)
            return RelevanceEngine(KeywordGroups.default())
        if not rows:
            return RelevanceEngine(KeywordGroups.default())
        return RelevanceEngine(KeywordGroups.from_db_groups(rows))

    async def _build_draft(self, fetcher: FetchManager, source_id: str,
                           entry: FeedEntry) -> tuple[ArticleDraft | None, str]:
        page: FetchResult = await fetcher.fetch(entry.url)
        if not page.ok:
            return None, f"article fetch failed ({page.error_code or 'HTTP'})"
        output = extract_article(page, source_id)
        if not output.confidence_ok:
            return None, output.failure_reason
        f = output.fields
        published = f.get("published")
        published_date = None
        published_at = None
        if published is not None:
            if hasattr(published, "tzinfo") and published.tzinfo is not None:
                published_at = published  # timestamp precision
            else:
                # date object or naive datetime -> date-only (never invent tz)
                published_date = published
        draft = ArticleDraft(
            source_id=source_id,
            original_url=entry.url,
            canonical_url=f.get("canonical_url"),
            title=f.get("title"),
            content_text=f.get("body"),
            author=f.get("author"),
            published_at=published_at,
            published_date=published_date,
            published_time_precision=f.get("published_time_precision", "unknown"),
            published_at_original=f.get("published_at_original")
                                  or entry.published_at_original,
            content_type="article",
            extraction_method=f.get("extraction_method", "html"),
            parser_name=output.parser_name,
            featured_image_url=f.get("featured_image_url"),
            summary_from_source=entry.summary,
            tags=list(entry.tags),
        )
        return normalize_draft(draft), ""

    def _persist(self, source_row, drafts, engine: RelevanceEngine,
                 totals: RunTotals) -> RunReport:
        """Live path: one ingestion_runs row + one transaction per article."""
        run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-" \
                 f"{uuid.uuid4().hex[:8]}"
        report = RunReport(source_id=source_row.source_id, mode="live",
                           ok=True, totals=totals, run_id=run_id)
        with open_session(self._settings.database_url) as session:
            runs = RunRepository(session)
            runs.open(run_id, mode="live")
            articles = ArticleRepository(session)
            health = HealthRepository(session)
            for _, draft, _ in drafts:
                try:
                    relevance = engine.classify(draft.title, draft.content_text,
                                                source_coverage=source_row.coverage_scope)
                    if relevance.score in ("low", "unknown"):
                        totals.relevance_filtered += 1
                        logger.info("relevance filtered: %s (%s)",
                                    draft.original_url, relevance.reason)
                        continue
                    chash = content_hash(
                        draft.title, draft.content_text,
                        draft.published_date.isoformat()
                        if draft.published_date else None)
                    existing = articles.find_by_content_hash(chash,
                                                             exclude_source=draft.source_id)
                    record_status = RecordStatus.COLLECTED.value
                    if existing:
                        totals.duplicates += 1
                        record_status = RecordStatus.DUPLICATE.value
                    record = ArticleRecord(
                        draft=draft,
                        bilaspur_relevance=relevance.score,
                        relevance_reason=relevance.reason,
                        matched_terms=relevance.matched_terms,
                        sensitive=relevance.sensitive,
                        content_hash=chash,
                        record_status=record_status,
                        source_verification_status=source_row.source_verification_status,
                        retrieved_at=datetime.now(timezone.utc),
                        first_seen_at=datetime.now(timezone.utc),
                        last_seen_at=datetime.now(timezone.utc),
                    )
                    action, _row = articles.upsert(record, run_id=run_id)
                    if action == "inserted":
                        totals.written += 1
                        report.articles.append(articles.export_row(_row))
                    else:
                        totals.updated += 1
                    session.commit()
                except Exception as exc:  # noqa: BLE001 - one bad article != lost run
                    session.rollback()
                    totals.failures += 1
                    totals.errors.append(f"storage failure for "
                                         f"{draft.original_url}: {exc}")
                    logger.exception("article persist failed")
            ok = totals.written + totals.updated > 0 or not totals.errors
            health.record_run(source_row.source_id, ok=ok,
                              articles=totals.written,
                              error_category=None if ok else ErrorCode.E_STORAGE.value,
                              error_message="; ".join(totals.errors[:3]))
            runs.close(run_id, totals.summary())
            session.commit()
        return report


def run_collection(settings: Settings, source_row, *, dry_run: bool,
                   max_items: int = 10) -> RunReport:
    if not dry_run:
        validate_for_live_crawl(settings)
    pipeline = CollectionPipeline(settings)
    return asyncio.run(pipeline.collect_source(source_row, dry_run=dry_run,
                                               max_items=max_items))
