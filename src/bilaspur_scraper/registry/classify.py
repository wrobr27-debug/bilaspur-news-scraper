"""Deterministic URL and workbook-text classification (`PLAN.md` §4, §5).

Pure functions only - no network, no I/O. Every rule is documented in
`PLAN.md` §4.1 and `ARCHITECTURE.md`; changes belong in tests first.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..schemas.enums import (
    ActivityStatus,
    CoverageScope,
    Priority,
    SourceType,
    SourceVerificationStatus,
    UrlKind,
)

_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Stable human-readable id, e.g. 'The Bilasa Times' -> 'the-bilasa-times'."""
    cleaned = _SLUG_CLEAN.sub("-", name.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", cleaned)


def classify_url(url: str) -> UrlKind:
    """Classify a registry URL. Mirrors `PLAN.md` §4.1 URL classes."""
    lowered = url.strip().lower()
    parsed = urlparse(lowered)
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path

    if host == "google.com" and parsed.path.startswith("/search"):
        return UrlKind.SEARCH_STUB
    if host == "linktr.ee":
        return UrlKind.AGGREGATOR
    if "/video/" in path:
        return UrlKind.VIDEO_SECTION
    if "/topic/" in path:
        return UrlKind.TOPIC
    if "/category/" in path:
        return UrlKind.SECTION
    # Known regional Bilaspur section paths (Nai Dunia, NDTV MPCG,
    # Live Hindustan) use deep paths without /category/.
    if re.search(r"/chhattisgarh(-news)?/bilaspur", path):
        return UrlKind.SECTION
    if host == "bilaspur.gov.in" and path not in ("", "/"):
        return UrlKind.SUB_PATH
    return UrlKind.HOMEPAGE


def is_search_stub(url: str) -> bool:
    return classify_url(url) == UrlKind.SEARCH_STUB


def publisher_source_type(category: str) -> SourceType:
    """Map the workbook 'Category' text to a source_type."""
    c = category.strip().lower()
    if "broadcaster" in c:
        return SourceType.BROADCASTER
    if "aggregator" in c or "link hub" in c:
        return SourceType.AGGREGATOR
    if "directory lead" in c or "publisher lead" in c:
        return SourceType.LEAD
    if "national" in c:
        return SourceType.PUBLISHER_NATIONAL
    if "regional" in c:
        return SourceType.PUBLISHER_REGIONAL
    return SourceType.PUBLISHER_LOCAL


def institution_source_type(type_text: str) -> SourceType:
    """Map the workbook 'Type' text to a source_type."""
    t = type_text.strip().lower()
    mapping: list[tuple[tuple[str, ...], SourceType]] = [
        (("district government",), SourceType.DISTRICT_GOVERNMENT),
        (("police",), SourceType.POLICE),
        (("election",), SourceType.ELECTION_OFFICE),
        (("public-sector", "public sector"), SourceType.PSU),
        (("central university",), SourceType.CENTRAL_UNIVERSITY),
        (("state university",), SourceType.STATE_UNIVERSITY),
        (("urban local body",), SourceType.URBAN_LOCAL_BODY),
        (("district local government",), SourceType.DISTRICT_LOCAL_GOVERNMENT),
        (("judiciary",), SourceType.JUDICIARY),
        (("industry association",), SourceType.INDUSTRY_ASSOCIATION),
        (("trade association",), SourceType.TRADE_ASSOCIATION),
        (("state government",), SourceType.STATE_GOVERNMENT),
    ]
    for needles, result in mapping:
        if any(n in t for n in needles):
            return result
    raise ValueError(f"unmapped institution type: {type_text!r}")


def source_verification_status_from_text(text: str) -> SourceVerificationStatus:
    """Conservative, deterministic mapping of workbook verification text.

    Observed activity is never treated as identity verification.
    """
    t = text.strip().lower()
    if "search link" in t or "search/discovery" in t or "discovery search" in t:
        return SourceVerificationStatus.SEARCH_LINK_ONLY
    if "portal link" in t:
        return SourceVerificationStatus.PORTAL_LINKED
    if "publisher-linked" in t or "publisher linked" in t:
        return SourceVerificationStatus.PUBLISHER_LINKED
    if "official" in t:
        return SourceVerificationStatus.VERIFIED_OFFICIAL
    return SourceVerificationStatus.UNVERIFIED


def activity_status_from_text(text: str, url_kind: UrlKind) -> ActivityStatus:
    """Deterministic activity-status mapping. Stubs/aggregators are parked."""
    if url_kind in (UrlKind.SEARCH_STUB, UrlKind.AGGREGATOR):
        return ActivityStatus.NOT_COLLECTABLE_VIA_CURRENT_METHOD
    t = text.strip().lower()
    if "active" in t:
        # Activity observed, but cadence is not independently verified.
        return ActivityStatus.ACTIVE_UNKNOWN_CADENCE
    return ActivityStatus.PENDING


def coverage_scope_from_text(text: str, *, fallback: CoverageScope) -> CoverageScope:
    """Ordered keyword mapping of 'Coverage / relevance' or 'Scope / relevance'."""
    t = text.strip().lower()
    if "statewide" in t:
        return CoverageScope.STATEWIDE
    if "national" in t:
        return CoverageScope.NATIONAL
    if "regional" in t:
        return CoverageScope.REGIONAL
    if "nearby" in t:
        return CoverageScope.NEARBY
    if "district" in t or "headquarters" in t or "constituency" in t:
        return CoverageScope.DISTRICT
    if "city" in t:
        return CoverageScope.CITY
    return fallback


def priority_for(source_type: SourceType, category: str) -> Priority:
    """Monitoring Priorities labels applied to sources (`PLAN.md` §18).

    Mapping (documented, deterministic - derived from the Monitoring
    Priorities sheet scope text):
      P1 - official administration/police/municipal/judiciary/election +
           major Bilaspur-focused local publishers.
      P2 - universities and public-sector industry (SECL/NTPC scope).
      P3 - associations, regional/national publishers, candidates, leads.
    """
    if source_type in (
        SourceType.DISTRICT_GOVERNMENT,
        SourceType.POLICE,
        SourceType.URBAN_LOCAL_BODY,
        SourceType.DISTRICT_LOCAL_GOVERNMENT,
        SourceType.ELECTION_OFFICE,
        SourceType.JUDICIARY,
        SourceType.STATE_GOVERNMENT,
    ):
        return Priority.P1
    if source_type in (
        SourceType.PSU,
        SourceType.CENTRAL_UNIVERSITY,
        SourceType.STATE_UNIVERSITY,
    ):
        return Priority.P2
    if source_type == SourceType.PUBLISHER_LOCAL:
        c = category.strip().lower()
        if "focused" in c:
            return Priority.P1
        return Priority.P3
    if source_type == SourceType.AGGREGATOR:
        return Priority.P3
    return Priority.P3
