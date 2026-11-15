"""Pydantic contracts for registry entities (`PLAN.md` §5, `ARCHITECTURE.md` §6.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ActivityStatus,
    CollectionMethod,
    CoverageScope,
    LifecycleState,
    Platform,
    Priority,
    SourceType,
    SourceVerificationStatus,
    UrlKind,
)


class SourceRecord(BaseModel):
    """One canonical registry row, before persistence."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    source_name: str
    source_type: SourceType
    platform: Platform = Platform.WEBSITE
    url: str
    url_kind: UrlKind
    coverage_scope: CoverageScope
    priority: Priority
    collection_method: CollectionMethod = CollectionMethod.NONE
    source_verification_status: SourceVerificationStatus = (
        SourceVerificationStatus.UNVERIFIED
    )
    activity_status: ActivityStatus = ActivityStatus.PENDING
    lifecycle_state: LifecycleState = LifecycleState.CANDIDATE
    enabled: bool = False
    notes: str = ""
    origin_sheet: str
    origin_row: int

    @classmethod
    def from_row(cls, row) -> "SourceRecord":
        """Rebuild the canonical record from a persisted `Source` row."""
        return cls(
            source_id=row.source_id,
            source_name=row.source_name,
            source_type=SourceType(row.source_type),
            platform=Platform(row.platform),
            url=row.url,
            url_kind=UrlKind(row.url_kind),
            coverage_scope=CoverageScope(row.coverage_scope),
            priority=Priority(row.priority),
            collection_method=CollectionMethod(row.collection_method),
            source_verification_status=SourceVerificationStatus(
                row.source_verification_status),
            activity_status=ActivityStatus(row.activity_status),
            lifecycle_state=LifecycleState(row.lifecycle_state),
            enabled=bool(row.enabled),
            notes=row.notes,
            origin_sheet=row.origin_sheet,
            origin_row=row.origin_row,
        )


class SourceAlias(BaseModel):
    """Alternate name pointing at one canonical `source_id`."""

    model_config = ConfigDict(frozen=True)

    alias: str
    source_id: str


class KeywordGroup(BaseModel):
    """Keyword Watchlist group used by the relevance engine."""

    model_config = ConfigDict(frozen=True)

    group_name: str
    terms: list[str]
    coverage_purpose: str
    usage_notes: str = ""


class DiscoveryBacklogItem(BaseModel):
    """Row from Additional Monitoring Targets - never crawled automatically."""

    model_config = ConfigDict(frozen=True)

    category: str
    target: str
    source_type: str
    signals: str
    preferred_locations: str
    priority: str
    registry_status: str
    notes: str = ""


class VerificationQueueItem(BaseModel):
    """Read-only mirror of the Verification Queue sheet (human work)."""

    model_config = ConfigDict(frozen=True)

    priority: str
    task: str
    why: str
    evidence: str
    state: str
    action: str


class QuarantinedRow(BaseModel):
    """A workbook row that could not be imported, with the reason."""

    model_config = ConfigDict(frozen=True)

    origin_sheet: str
    origin_row: int
    reason: str
    raw_name: str = ""


class ImportReport(BaseModel):
    """Summary of one `import-registry` invocation."""

    model_config = ConfigDict(frozen=True)

    sources_imported: int
    sources_updated: int
    aliases: int
    keyword_groups: int
    backlog_items: int
    verification_queue_items: int
    quarantined: list[QuarantinedRow]
    lifecycle_counts: dict[str, int]
    enabled_count: int = 0
