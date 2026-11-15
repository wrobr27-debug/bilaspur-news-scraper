"""Repository layer - the only module that touches SQL (`ARCHITECTURE.md` D7).

Guarantees enforced here:
- import is idempotent and **never** sets `enabled = true`;
- import never downgrades an advanced lifecycle state;
- every lifecycle change goes through the state machine, with
  `verified -> enabled` requiring `verified_by` + evidence;
- verification evidence records budget fields for auditability.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import LifecycleError
from ..registry.lifecycle import assert_transition
from ..schemas.enums import LifecycleState, SourceVerificationStatus
from ..schemas.source import (
    DiscoveryBacklogItem,
    KeywordGroup,
    SourceAlias,
    SourceRecord,
    VerificationQueueItem,
)
from .models import (
    Article,
    ArticleRevision,
    ArticleUrl,
    DiscoveryBacklog,
    IngestionRun,
    KeywordGroup,
    Source,
    SourceAlias,
    SourceHealth,
    SourceVerification,
    VerificationQueue,
    utcnow,
)


#: Verification statuses ordered from weakest to strongest. A workbook
#: re-import may only ever *raise* a stored status, never lower it: the
#: workbook is a snapshot of the operator's notes, while the stored value
#: may already reflect a live verification with recorded evidence.
_VERIFICATION_STATUS_RANK = {
    SourceVerificationStatus.UNVERIFIED.value: 0,
    SourceVerificationStatus.SEARCH_LINK_ONLY.value: 1,
    SourceVerificationStatus.PORTAL_LINKED.value: 2,
    SourceVerificationStatus.PUBLISHER_LINKED.value: 3,
    SourceVerificationStatus.VERIFIED_OFFICIAL.value: 4,
}


def _merge_verification_status(stored: str | None, incoming: str) -> str:
    """Keep the stronger of the stored and the workbook status.

    Import is idempotent and non-destructive (`PLAN.md` Phase 0): re-importing
    the workbook must not reset a source that live verification already
    promoted. `verified_official` > `publisher_linked` > `portal_linked` >
    `search_link_only` > `unverified`.
    """
    if not stored:
        return incoming
    stored_rank = _VERIFICATION_STATUS_RANK.get(stored, 0)
    incoming_rank = _VERIFICATION_STATUS_RANK.get(incoming, 0)
    return stored if stored_rank >= incoming_rank else incoming


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- import (idempotent) -------------------------------------------------

    def upsert_from_import(self, record: SourceRecord) -> str:
        """Insert or refresh a registry row. Returns 'inserted' or 'updated'.

        Never advances a state past what classification implies, and never
        touches `enabled` toward true.
        """
        existing = self._session.get(Source, record.source_id)
        if existing is None:
            self._session.add(Source(
                source_id=record.source_id,
                source_name=record.source_name,
                source_type=record.source_type.value,
                platform=record.platform.value,
                url=record.url,
                url_kind=record.url_kind.value,
                coverage_scope=record.coverage_scope.value,
                priority=record.priority.value,
                collection_method=record.collection_method.value,
                source_verification_status=record.source_verification_status.value,
                activity_status=record.activity_status.value,
                lifecycle_state=record.lifecycle_state.value,
                enabled=False,
                notes=record.notes,
                origin_sheet=record.origin_sheet,
                origin_row=record.origin_row,
            ))
            return "inserted"

        # Idempotent refresh: descriptive fields only; lifecycle is kept
        # unless the stored row is still at its initial value.
        existing.source_name = record.source_name
        existing.source_type = record.source_type.value
        existing.platform = record.platform.value
        existing.url = record.url
        existing.url_kind = record.url_kind.value
        existing.coverage_scope = record.coverage_scope.value
        existing.priority = record.priority.value
        existing.source_verification_status = _merge_verification_status(
            existing.source_verification_status,
            record.source_verification_status.value,
        )
        existing.activity_status = record.activity_status.value
        existing.notes = record.notes
        existing.origin_sheet = record.origin_sheet
        existing.origin_row = record.origin_row
        if existing.lifecycle_state in (
            LifecycleState.CANDIDATE.value,
            LifecycleState.EXCLUDED.value,
        ):
            existing.lifecycle_state = record.lifecycle_state.value
        existing.enabled = bool(existing.enabled)
        return "updated"

    # -- reads ---------------------------------------------------------------

    def get(self, source_id: str) -> Optional[Source]:
        return self._session.get(Source, source_id)

    def require(self, source_id: str) -> Source:
        source = self.get(source_id)
        if source is None:
            raise LifecycleError(f"unknown source_id: {source_id!r}")
        return source

    def all_sources(self) -> Sequence[Source]:
        return self._session.scalars(
            select(Source).order_by(Source.source_id)
        ).all()

    def count_by_state(self) -> dict[str, int]:
        rows = self._session.execute(
            select(Source.lifecycle_state, func.count())
            .group_by(Source.lifecycle_state)
        ).all()
        return {state: int(count) for state, count in rows}

    def enabled_count(self) -> int:
        return int(self._session.scalar(
            select(func.count()).select_from(Source).where(Source.enabled.is_(True))
        ) or 0)

    # -- browser approval (D10) ---------------------------------------------

    def set_browser_approval(self, source_id: str, approved: bool, *,
                             by: str | None = None) -> Source:
        """Record (or revoke) the per-source browser approval.

        D10: a browser is used only for sources the operator explicitly
        approved; the approver and timestamp stay on the row.
        """
        source = self.require(source_id)
        source.browser_approved = bool(approved)
        source.browser_approved_by = by if approved else None
        source.browser_approved_at = utcnow() if approved else None
        self._session.flush()
        return source

    def browser_approved_sources(self) -> Sequence[Source]:
        return self._session.scalars(
            select(Source).where(Source.browser_approved.is_(True))
            .order_by(Source.priority, Source.source_id)
        ).all()

    # -- lifecycle transitions ------------------------------------------------

    def set_state(self, source_id: str, target: LifecycleState, *,
                  reason: str | None = None,
                  verified_by: str | None = None,
                  verification_evidence: str | None = None,
                  verification_method: str | None = None) -> Source:
        """Apply a state-machine-validated transition and persist it."""
        source = self.require(source_id)
        current = LifecycleState(source.lifecycle_state)
        assert_transition(current, target, reason=reason)

        if target is LifecycleState.ENABLED:
            if not verified_by:
                raise LifecycleError("enabling a source requires verified_by")
            if source.lifecycle_state != LifecycleState.VERIFIED.value:
                raise LifecycleError(
                    "source must be 'verified' (with recorded evidence) before enablement"
                )
        if target is LifecycleState.VERIFIED:
            if not (verification_evidence and verified_by):
                raise LifecycleError(
                    "verifying a source requires evidence and reviewer"
                )
            source.verification_evidence = verification_evidence
            source.verification_checked_at = utcnow()
            source.verification_method = verification_method or "manual"

        source.lifecycle_state = target.value
        if target is LifecycleState.ENABLED:
            source.verified_by = verified_by
            source.enabled = True
        elif current is LifecycleState.ENABLED and target is not LifecycleState.ENABLED:
            source.enabled = False
        self._session.flush()
        return source


class AliasRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, alias: SourceAlias) -> int:
        exists = self._session.scalar(
            select(SourceAlias).where(
                SourceAlias.alias == alias.alias,
                SourceAlias.source_id == alias.source_id,
            )
        )
        if exists is None:
            self._session.add(SourceAlias(alias=alias.alias, source_id=alias.source_id))
            return 1
        return 0

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(SourceAlias)) or 0)


class KeywordGroupRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, group: KeywordGroup) -> int:
        existing = self._session.scalar(
            select(KeywordGroup).where(KeywordGroup.group_name == group.group_name)
        )
        joined = "; ".join(group.terms)
        if existing is None:
            self._session.add(KeywordGroup(
                group_name=group.group_name,
                terms=joined,
                coverage_purpose=group.coverage_purpose,
                usage_notes=group.usage_notes,
            ))
            return 1
        existing.terms = joined
        existing.coverage_purpose = group.coverage_purpose
        existing.usage_notes = group.usage_notes
        return 0

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(KeywordGroup)) or 0)

    def terms_rows(self) -> list[tuple[str, list[str]]]:
        """(group_name, terms) rows for the relevance engine."""
        rows = self._session.scalars(
            select(KeywordGroup).order_by(KeywordGroup.group_name)
        ).all()
        return [
            (row.group_name, [t.strip() for t in row.terms.split(";") if t.strip()])
            for row in rows
        ]


class BacklogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, item: DiscoveryBacklogItem) -> int:
        existing = self._session.scalar(
            select(DiscoveryBacklog).where(
                DiscoveryBacklog.category == item.category,
                DiscoveryBacklog.target == item.target,
            )
        )
        if existing is None:
            self._session.add(DiscoveryBacklog(
                category=item.category,
                target=item.target,
                source_type=item.source_type,
                signals=item.signals,
                preferred_locations=item.preferred_locations,
                priority=item.priority,
                registry_status=item.registry_status,
                notes=item.notes,
            ))
            return 1
        existing.source_type = item.source_type
        existing.signals = item.signals
        existing.preferred_locations = item.preferred_locations
        existing.priority = item.priority
        existing.registry_status = item.registry_status
        existing.notes = item.notes
        return 0

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(DiscoveryBacklog)) or 0)


class VerificationQueueRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_all(self, items: list[VerificationQueueItem]) -> int:
        """Read-only mirror: rebuilt wholesale on each import."""
        self._session.query(VerificationQueue).delete()
        for item in items:
            self._session.add(VerificationQueue(
                priority=item.priority,
                task=item.task,
                why=item.why,
                evidence=item.evidence,
                state=item.state,
                action=item.action,
            ))
        return len(items)

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(VerificationQueue)) or 0)


class VerificationRepository:
    """Evidence rows - written by the Phase 1 verification engine; schema
    and the auditable budget fields exist from Phase 0."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, source_id: str, *, result: str, reviewer: str | None = None,
              method: str | None = None, http_status: int | None = None,
              robots_allowed: bool | None = None, feed_found: bool | None = None,
              sitemap_found: bool | None = None, listing_found: bool | None = None,
              parser_name: str | None = None, evidence: str | None = None,
              blocking_reason: str | None = None, requests_consumed: int | None = None,
              request_budget: int | None = None, browser_used: bool | None = None,
              verification_policy_version: str | None = None) -> SourceVerification:
        row = SourceVerification(
            source_id=source_id,
            method=method,
            http_status=http_status,
            robots_allowed=robots_allowed,
            feed_found=feed_found,
            sitemap_found=sitemap_found,
            listing_found=listing_found,
            parser_name=parser_name,
            evidence=evidence,
            result=result,
            reviewer=reviewer,
            blocking_reason=blocking_reason,
            requests_consumed=requests_consumed,
            request_budget=request_budget,
            browser_used=browser_used,
            verification_policy_version=verification_policy_version,
        )
        self._session.add(row)
        self._session.flush()
        return row


class RunRepository:
    """Run ledger. Live runs only - dry-runs write no row (D5)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def open(self, run_id: str, mode: str = "live") -> IngestionRun:
        if mode == "dry":
            raise LifecycleError("dry-runs must not write an ingestion_runs row (D5)")
        row = IngestionRun(run_id=run_id, mode=mode)
        self._session.add(row)
        self._session.flush()
        return row

    def close(self, run_id: str, totals: str) -> None:
        run = self._session.get(IngestionRun, run_id)
        if run is None:
            raise LifecycleError(f"unknown run: {run_id!r}")
        run.finished_at = utcnow()
        run.totals = totals

    def mark_interrupted(self) -> int:
        """Startup recovery: runs with no finished_at are marked interrupted."""
        open_runs = self._session.scalars(
            select(IngestionRun).where(IngestionRun.finished_at.is_(None))
        ).all()
        for run in open_runs:
            run.finished_at = utcnow()
            run.totals = (run.totals + " | interrupted").strip(" |")
        return len(open_runs)


def _join(values: Sequence[str]) -> str:
    return "; ".join(values)


class ArticleRepository:
    """Per-article transactional persistence (`ARCHITECTURE.md` §7.4).

    One upsert = one transaction; a failure on one article never loses
    the rest of the run.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_canonical(self, source_id: str, canonical_url: str) -> Optional[Article]:
        return self._session.scalars(
            select(Article).where(
                Article.source_id == source_id,
                Article.canonical_url == canonical_url,
            )
        ).first()

    def find_by_content_hash(self, content_hash: str,
                             *, exclude_source: str | None = None) -> Sequence[Article]:
        """Content-hash is a dedup *signal*, never a uniqueness rule."""
        stmt = select(Article).where(Article.content_hash == content_hash)
        if exclude_source:
            stmt = stmt.where(Article.source_id != exclude_source)
        return self._session.scalars(stmt.limit(10)).all()

    def upsert(self, record, *, run_id: str | None = None) -> tuple[str, Article]:
        """Insert or refresh one article. Returns ('inserted'|'updated', row).

        `record` is a validated `ArticleRecord` pydantic instance. Callers
        commit their own per-article transaction.
        """
        draft = record.draft
        existing = self.find_by_canonical(draft.source_id, draft.canonical_url or draft.original_url)
        if existing is None:
            article = Article(
                source_id=draft.source_id,
                canonical_url=draft.canonical_url or draft.original_url,
                original_url=draft.original_url,
                title=draft.title,
                content_text=draft.content_text,
                summary_from_source=draft.summary_from_source,
                author=draft.author,
                language=draft.language,
                content_type=draft.content_type,
                published_at=draft.published_at,
                published_date=(draft.published_date.isoformat()
                                if draft.published_date else None),
                published_time_precision=draft.published_time_precision,
                published_at_original=draft.published_at_original,
                location_mentions=_join(draft.location_mentions),
                bilaspur_relevance=record.bilaspur_relevance,
                relevance_reason=record.relevance_reason,
                matched_terms=_join(record.matched_terms),
                sensitive=record.sensitive,
                content_hash=record.content_hash,
                record_status=record.record_status,
                extraction_method=draft.extraction_method,
                parser_name=draft.parser_name,
                parser_version=draft.parser_version,
                featured_image_url=draft.featured_image_url,
                tags=_join(draft.tags),
                categories=_join(draft.categories),
                source_verification_status=record.source_verification_status,
                run_id=run_id,
            )
            self._session.add(article)
            self._session.flush()
            if draft.canonical_url and draft.original_url:
                self._session.add(ArticleUrl(
                    article_id=article.article_id,
                    url=draft.original_url,
                    normalized_url=draft.original_url,
                    url_kind="original",
                ))
            # Revision 1 preserves the original text verbatim.
            self._session.add(ArticleRevision(
                article_id=article.article_id,
                revision=1,
                title=draft.title,
                content_text=draft.content_text,
                content_hash=record.content_hash,
                change_note="first collection",
            ))
            return "inserted", article

        # Known canonical URL: touch timestamps; record a revision only if
        # the normalized text actually changed.
        existing.refresh_seen(run_id)
        changed = (existing.title, existing.content_text) != (draft.title, draft.content_text)
        if changed and draft.content_text:
            max_rev = self._session.scalar(
                select(func.max(ArticleRevision.revision)).where(
                    ArticleRevision.article_id == existing.article_id)
            ) or 0
            self._session.add(ArticleRevision(
                article_id=existing.article_id,
                revision=int(max_rev) + 1,
                title=draft.title,
                content_text=draft.content_text,
                content_hash=record.content_hash,
                change_note="content changed on re-collection",
            ))
            existing.title = draft.title
            existing.content_text = draft.content_text
        existing.bilaspur_relevance = record.bilaspur_relevance
        existing.relevance_reason = record.relevance_reason
        existing.record_status = record.record_status
        return "updated", existing

    def count_all(self) -> int:
        return int(self._session.scalar(
            select(func.count()).select_from(Article)) or 0)

    def count_by_source(self, source_id: str) -> int:
        return int(self._session.scalar(
            select(func.count()).select_from(Article).where(
                Article.source_id == source_id)) or 0)

    def recent(self, source_id: str | None = None, limit: int = 20) -> Sequence[Article]:
        stmt = select(Article).order_by(Article.article_id.desc()).limit(limit)
        if source_id:
            stmt = stmt.where(Article.source_id == source_id)
        return self._session.scalars(stmt).all()

    def export_row(self, article: Article) -> dict:
        """JSON-safe row for CLI display. Raw text is truncated for logs."""
        return {
            "article_id": article.article_id,
            "source_id": article.source_id,
            "title": article.title,
            "url": article.canonical_url,
            "published_date": article.published_date,
            "language": article.language,
            "relevance": article.bilaspur_relevance,
            "record_status": article.record_status,
            "sensitive": article.sensitive,
            "content_chars": len(article.content_text or ""),
        }


class HealthRepository:
    """source_health upsert after every live run."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_run(self, source_id: str, *, ok: bool, articles: int,
                   error_category: str | None = None,
                   error_message: str | None = None,
                   error_code=None) -> SourceHealth:
        row = self._session.get(SourceHealth, source_id)
        if row is None:
            row = SourceHealth(source_id=source_id)
            self._session.add(row)
        now = utcnow()
        row.last_run_at = now
        row.articles_last_run = articles
        if ok:
            row.last_success_at = now
            row.consecutive_failures = 0
            row.last_error_category = None
            row.last_error_message = None
        else:
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            row.last_error_category = error_category or (
                error_code.value if error_code is not None else None)
            row.last_error_message = error_message
        row.articles_total = (row.articles_total or 0) + max(0, articles)
        self._session.flush()
        return row

    def get(self, source_id: str) -> Optional[SourceHealth]:
        return self._session.get(SourceHealth, source_id)
