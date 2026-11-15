"""SQLAlchemy 2.x ORM models - Phase 0 registry schema (`PLAN.md` §11).

Phase 0 creates the registry/verification/run-ledger tables. Article
tables arrive with the persistence engine (Phase 1/5) as a separate
migration. Enum values are stored as TEXT with CHECK-free application-side
validation (one canonical enum definition, per `PLAN.md` §11.1).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_name: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="website")
    url: Mapped[str] = mapped_column(Text)
    url_kind: Mapped[str] = mapped_column(String(32))
    coverage_scope: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(8), index=True)
    collection_method: Mapped[str] = mapped_column(String(32), default="none")
    source_verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    activity_status: Mapped[str] = mapped_column(String(48), default="pending")
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verification_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: JSON endpoints discovered during verification: {"feed_url": ...}
    collection_endpoints: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: D10: per-source browser approval (Playwright rendering). False by
    #: default - a browser is never used without an explicit operator
    #: decision recorded here. The server default keeps the column
    #: addable to older database files.
    browser_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"),
        index=True)
    browser_approved_by: Mapped[str | None] = mapped_column(String(128),
                                                             nullable=True)
    browser_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    origin_sheet: Mapped[str] = mapped_column(String(64))
    origin_row: Mapped[int] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceAlias(Base):
    __tablename__ = "source_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.source_id"))
    __table_args__ = (UniqueConstraint("alias", "source_id", name="uq_alias_source"),)


class SourceVerification(Base):
    """Verification evidence history (`ARCHITECTURE.md` §4.4, D17).

    Phase 0 creates the table; rows are written by the Phase 1
    verification engine. Budget fields make the request cap auditable.
    """

    __tablename__ = "source_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.source_id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feed_found: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sitemap_found: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    listing_found: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    blocking_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requests_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_policy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)


class KeywordGroup(Base):
    __tablename__ = "keyword_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_name: Mapped[str] = mapped_column(Text, unique=True)
    terms: Mapped[str] = mapped_column(Text)  # ';'-joined verbatim terms
    coverage_purpose: Mapped[str] = mapped_column(Text, default="")
    usage_notes: Mapped[str] = mapped_column(Text, default="")


class DiscoveryBacklog(Base):
    __tablename__ = "discovery_backlog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, default="")
    signals: Mapped[str] = mapped_column(Text, default="")
    preferred_locations: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(8), default="")
    registry_status: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class VerificationQueue(Base):
    """Read-only mirror of the workbook's human Verification Queue."""

    __tablename__ = "verification_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    priority: Mapped[str] = mapped_column(String(8), default="")
    task: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(Text, default="")


class IngestionRun(Base):
    """Run ledger - live runs only (`ARCHITECTURE.md` D5: dry-runs write no row)."""

    __tablename__ = "ingestion_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="live")
    totals: Mapped[str] = mapped_column(Text, default="")


class Article(Base):
    """One canonical article per (source, canonical_url) (`ARCHITECTURE.md` §7.4).

    Metadata only - raw bodies are never persisted (D13). The first
    revision preserves the original text verbatim.
    """

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("source_id", "canonical_url", name="uq_article_canonical"),
        Index("ix_articles_content_hash", "content_hash"),
    )

    article_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.source_id"), index=True)
    canonical_url: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_from_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="und")
    content_type: Mapped[str] = mapped_column(String(32), default="article")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    published_time_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    published_at_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_mentions: Mapped[str] = mapped_column(Text, default="")
    bilaspur_relevance: Mapped[str] = mapped_column(String(16), index=True)
    relevance_reason: Mapped[str] = mapped_column(Text, default="")
    matched_terms: Mapped[str] = mapped_column(Text, default="")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(71))
    record_status: Mapped[str] = mapped_column(String(32), default="collected", index=True)
    extraction_method: Mapped[str] = mapped_column(String(64), default="html")
    parser_name: Mapped[str] = mapped_column(String(128), default="")
    parser_version: Mapped[str] = mapped_column(String(16), default="1")
    featured_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[str] = mapped_column(Text, default="")
    source_verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def refresh_seen(self, run_id: str | None) -> None:
        """Re-collection of a known canonical URL: touch timestamps only."""
        self.last_seen_at = utcnow()
        self.retrieved_at = utcnow()
        if run_id:
            self.run_id = run_id


class ArticleUrl(Base):
    """Every URL variant observed for an article (dedup signal #2)."""

    __tablename__ = "article_urls"
    __table_args__ = (
        UniqueConstraint("article_id", "normalized_url", name="uq_article_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.article_id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(String(512))
    url_kind: Mapped[str] = mapped_column(String(16), default="original")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArticleRevision(Base):
    """Immutable text snapshots; revision 1 is the original verbatim text."""

    __tablename__ = "article_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.article_id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(71))
    change_note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        UniqueConstraint("article_id", "revision", name="uq_article_revision"),
    )


class ArticleRelation(Base):
    """Syndication / update links between articles (dedup signal #4 groundwork)."""

    __tablename__ = "article_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.article_id"), index=True)
    to_article_id: Mapped[int] = mapped_column(ForeignKey("articles.article_id"))
    relation: Mapped[str] = mapped_column(String(32))  # 'syndication' | 'update'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evidence: Mapped[str] = mapped_column(Text, default="")


class SourceHealth(Base):
    """Per-source collection health, updated after every live run."""

    __tablename__ = "source_health"

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.source_id"), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    articles_last_run: Mapped[int] = mapped_column(Integer, default=0)
    articles_total: Mapped[int] = mapped_column(Integer, default=0)
