"""Workbook -> registry records (`PLAN.md` §4, §5, `SKILLS.md` §3).

Reads the candidate registry workbook once and produces typed records:
sources (Media Publishers + Institutions & Business), aliases, keyword
groups, discovery backlog, and the read-only verification queue mirror.

Rules enforced here:
- every row is a *candidate*; nothing is verified, enabled, or crawled;
- Google-search stub rows and link hubs are parked as `excluded` with
  `activity_status = not_collectable_via_current_method`;
- malformed rows are quarantined with a reason (never silently dropped);
- import is idempotent and never downgrades an advanced lifecycle state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import openpyxl

from ..errors import ConfigError
from ..schemas.enums import (
    CollectionMethod,
    CoverageScope,
    LifecycleState,
    Platform,
    UrlKind,
)
from ..schemas.source import (
    DiscoveryBacklogItem,
    ImportReport,
    KeywordGroup,
    QuarantinedRow,
    SourceAlias,
    SourceRecord,
    VerificationQueueItem,
)
from . import aliases as alias_rules
from . import classify

logger = logging.getLogger(__name__)

SHEET_MEDIA = "Media Publishers"
SHEET_INSTITUTIONS = "Institutions & Business"
SHEET_KEYWORDS = "Keyword Watchlist"
SHEET_BACKLOG = "Additional Monitoring Targets"
SHEET_QUEUE = "Verification Queue"


def _cell(value: object) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _to_records(workbook: openpyxl.Workbook) -> ImportResult:
    """Parse the four import sheets into typed records."""
    result = ImportResult()
    _load_media(workbook, result)
    _load_institutions(workbook, result)
    _load_keywords(workbook, result)
    _load_backlog(workbook, result)
    _load_queue(workbook, result)
    result.aliases.extend(
        alias_rules.build_publisher_name_aliases_from(result.publishers)
    )
    result.aliases.extend(
        alias_rules.build_backlog_aliases(
            [item.target for item in result.backlog],
            [(p.source_name, p.source_id) for p in result.publishers if p.origin_sheet == SHEET_INSTITUTIONS],
        )
    )
    return result


class ImportResult:
    def __init__(self) -> None:
        self.publishers: list[SourceRecord] = []
        self.aliases: list[SourceAlias] = []
        self.keywords: list[KeywordGroup] = []
        self.backlog: list[DiscoveryBacklogItem] = []
        self.queue: list[VerificationQueueItem] = []
        self.quarantined: list[QuarantinedRow] = []

    @property
    def sources(self) -> list[SourceRecord]:
        return self.publishers


def _slug_for(name: str) -> str:
    # Multi-name rows take the first ' / ' part for the canonical slug.
    return classify.slugify(name.split("/")[0])


def _load_media(workbook: openpyxl.Workbook, result: "ImportResult") -> None:
    ws = workbook[SHEET_MEDIA]
    rows = list(ws.iter_rows(values_only=True))
    for idx, row in enumerate(rows[1:], start=2):
        name, category, url = _cell(row[0]), _cell(row[1]), _cell(row[2])
        if not name and not url:
            continue
        if not name or not url:
            result.quarantined.append(
                QuarantinedRow(origin_sheet=SHEET_MEDIA, origin_row=idx,
                               reason="missing name or URL", raw_name=name)
            )
            continue
        url_kind = classify.classify_url(url)
        source_type = classify.publisher_source_type(category)
        excluded = url_kind in (UrlKind.SEARCH_STUB, UrlKind.AGGREGATOR)
        notes = " || ".join(filter(None, [_cell(row[9]), _cell(row[10])]))
        result.publishers.append(SourceRecord(
            source_id=_slug_for(name),
            source_name=name,
            source_type=source_type,
            platform=Platform.WEBSITE,
            url=url,
            url_kind=url_kind,
            coverage_scope=classify.coverage_scope_from_text(_cell(row[7]),
                                                            fallback=CoverageScope.CITY),
            priority=classify.priority_for(source_type, category),
            collection_method=CollectionMethod.NONE,
            source_verification_status=classify.source_verification_status_from_text(_cell(row[9])),
            activity_status=classify.activity_status_from_text(_cell(row[9]), url_kind),
            lifecycle_state=LifecycleState.EXCLUDED if excluded else LifecycleState.CANDIDATE,
            enabled=False,
            notes=notes,
            origin_sheet=SHEET_MEDIA,
            origin_row=idx,
        ))


def _load_institutions(workbook: openpyxl.Workbook, result: "ImportResult") -> None:
    ws = workbook[SHEET_INSTITUTIONS]
    rows = list(ws.iter_rows(values_only=True))
    for idx, row in enumerate(rows[1:], start=2):
        name, type_text, url = _cell(row[0]), _cell(row[1]), _cell(row[2])
        if not name and not url:
            continue
        if not name or not url:
            result.quarantined.append(
                QuarantinedRow(origin_sheet=SHEET_INSTITUTIONS, origin_row=idx,
                               reason="missing name or URL", raw_name=name)
            )
            continue
        try:
            source_type = classify.institution_source_type(type_text)
        except ValueError as exc:
            result.quarantined.append(
                QuarantinedRow(origin_sheet=SHEET_INSTITUTIONS, origin_row=idx,
                               reason=str(exc), raw_name=name)
            )
            continue
        url_kind = classify.classify_url(url)
        verif_text = _cell(row[8])
        notes = " || ".join(filter(None, [verif_text, _cell(row[9])]))
        result.publishers.append(SourceRecord(
            source_id=_slug_for(name),
            source_name=name,
            source_type=source_type,
            platform=Platform.WEBSITE,
            url=url,
            url_kind=url_kind,
            coverage_scope=classify.coverage_scope_from_text(_cell(row[7]),
                                                            fallback=CoverageScope.DISTRICT),
            priority=classify.priority_for(source_type, type_text),
            collection_method=CollectionMethod.NONE,
            source_verification_status=classify.source_verification_status_from_text(verif_text),
            activity_status=classify.activity_status_from_text(verif_text, url_kind),
            lifecycle_state=LifecycleState.CANDIDATE,
            enabled=False,
            notes=notes,
            origin_sheet=SHEET_INSTITUTIONS,
            origin_row=idx,
        ))


def _load_keywords(workbook: openpyxl.Workbook, result: "ImportResult") -> None:
    ws = workbook[SHEET_KEYWORDS]
    rows = list(ws.iter_rows(values_only=True))
    for idx, row in enumerate(rows[1:], start=2):
        group = _cell(row[0])
        terms_raw = _cell(row[1])
        if not group or not terms_raw:
            if group or terms_raw:
                result.quarantined.append(
                    QuarantinedRow(origin_sheet=SHEET_KEYWORDS, origin_row=idx,
                                   reason="missing group name or terms", raw_name=group)
                )
            continue
        terms = [t.strip() for t in terms_raw.split(";") if t.strip()]
        result.keywords.append(KeywordGroup(
            group_name=group,
            terms=terms,
            coverage_purpose=_cell(row[2]),
            usage_notes=_cell(row[3]),
        ))


def _load_backlog(workbook: openpyxl.Workbook, result: "ImportResult") -> None:
    ws = workbook[SHEET_BACKLOG]
    rows = list(ws.iter_rows(values_only=True))
    for idx, row in enumerate(rows[1:], start=2):
        category, target = _cell(row[0]), _cell(row[1])
        if not category and not target:
            continue
        if not target:
            result.quarantined.append(
                QuarantinedRow(origin_sheet=SHEET_BACKLOG, origin_row=idx,
                               reason="missing target", raw_name=category)
            )
            continue
        result.backlog.append(DiscoveryBacklogItem(
            category=category,
            target=target,
            source_type=_cell(row[2]),
            signals=_cell(row[3]),
            preferred_locations=_cell(row[4]),
            priority=_cell(row[5]),
            registry_status=_cell(row[6]),
            notes=_cell(row[7]),
        ))


def _load_queue(workbook: openpyxl.Workbook, result: "ImportResult") -> None:
    ws = workbook[SHEET_QUEUE]
    rows = list(ws.iter_rows(values_only=True))
    for idx, row in enumerate(rows[1:], start=2):
        if all(c is None for c in row):
            continue
        result.queue.append(VerificationQueueItem(
            priority=_cell(row[0]),
            task=_cell(row[1]),
            why=_cell(row[2]),
            evidence=_cell(row[3]),
            state=_cell(row[4]),
            action=_cell(row[5]),
        ))


def load_workbook_records(path: str) -> ImportResult:
    """Open the workbook and parse it. Raises ConfigError on failure."""
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except FileNotFoundError as exc:
        raise ConfigError(f"workbook not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001 - openpyxl raises various types
        raise ConfigError(f"cannot read workbook {path}: {exc}") from exc
    required = {SHEET_MEDIA, SHEET_INSTITUTIONS, SHEET_KEYWORDS, SHEET_BACKLOG, SHEET_QUEUE}
    missing = required - set(workbook.sheetnames)
    if missing:
        workbook.close()
        raise ConfigError(f"workbook is missing expected sheets: {sorted(missing)}")
    try:
        return _to_records(workbook)
    finally:
        workbook.close()


def stamp_checked_at() -> datetime:
    return datetime.now(timezone.utc)
