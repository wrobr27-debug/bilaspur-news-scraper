import pytest

from bilaspur_scraper.registry import classify
from bilaspur_scraper.schemas.enums import (
    ActivityStatus,
    CoverageScope,
    Priority,
    SourceType,
    SourceVerificationStatus,
    UrlKind,
)


@pytest.mark.parametrize("url,expected", [
    ("https://www.google.com/search?q=Sindhu+Youth+Media+Bilaspur", UrlKind.SEARCH_STUB),
    ("https://linktr.ee/inhnews", UrlKind.AGGREGATOR),
    ("https://www.naidunia.com/chhattisgarh/bilaspur", UrlKind.SECTION),
    ("https://vistaarnews.com/category/chhattisgarh/bilaspur/", UrlKind.SECTION),
    ("https://mpcg.ndtv.in/chhattisgarh-news/bilaspur", UrlKind.SECTION),
    ("https://www.livehindustan.com/chhattisgarh/bilaspur/news", UrlKind.SECTION),
    ("https://www.amarujala.com/video/chhattisgarh/bilaspur-chhattisgarh",
     UrlKind.VIDEO_SECTION),
    ("https://www.haribhoomi.com/topic/bilaspur-news", UrlKind.TOPIC),
    ("https://bilaspur.gov.in/en/deo-portal/", UrlKind.SUB_PATH),
    ("https://bilaspur.gov.in/en/public-utility-category/urban-body/", UrlKind.SUB_PATH),
    ("https://bilaspur.gov.in/", UrlKind.HOMEPAGE),
    ("https://thebilasatimes.in/", UrlKind.HOMEPAGE),
])
def test_classify_url(url, expected):
    assert classify.classify_url(url) == expected


def test_slugify():
    assert classify.slugify("The Bilasa Times") == "the-bilasa-times"
    assert classify.slugify("Nai Dunia – Bilaspur") == "nai-dunia-bilaspur"
    assert classify.slugify("South Eastern Coalfields Limited (SECL)") == \
        "south-eastern-coalfields-limited-secl"


def test_publisher_source_types():
    assert classify.publisher_source_type("Bilaspur-focused publisher") == \
        SourceType.PUBLISHER_LOCAL
    assert classify.publisher_source_type("Regional broadcaster") == \
        SourceType.BROADCASTER
    assert classify.publisher_source_type("National/regional publisher") == \
        SourceType.PUBLISHER_NATIONAL
    assert classify.publisher_source_type("Directory lead") == SourceType.LEAD


def test_institution_source_types():
    assert classify.institution_source_type("District government") == \
        SourceType.DISTRICT_GOVERNMENT
    assert classify.institution_source_type("Police department") == SourceType.POLICE
    assert classify.institution_source_type("Public-sector company") == SourceType.PSU
    assert classify.institution_source_type("Urban local body") == \
        SourceType.URBAN_LOCAL_BODY
    with pytest.raises(ValueError):
        classify.institution_source_type("Unknown thing")


def test_verification_status_mapping_is_conservative():
    # Observed activity is NOT identity verification.
    assert classify.source_verification_status_from_text("Website active") == \
        SourceVerificationStatus.UNVERIFIED
    assert classify.source_verification_status_from_text(
        "Website active; YouTube publisher-linked") == \
        SourceVerificationStatus.PUBLISHER_LINKED
    assert classify.source_verification_status_from_text(
        "Portal links reported; exact accounts pending") == \
        SourceVerificationStatus.PORTAL_LINKED
    assert classify.source_verification_status_from_text(
        "Website active; YouTube publisher-linked; Facebook search link only") == \
        SourceVerificationStatus.SEARCH_LINK_ONLY
    assert classify.source_verification_status_from_text(
        "Official X account identified") == SourceVerificationStatus.VERIFIED_OFFICIAL
    assert classify.source_verification_status_from_text("Unverified") == \
        SourceVerificationStatus.UNVERIFIED


def test_activity_status_parks_stubs():
    assert classify.activity_status_from_text(
        "Unverified", UrlKind.SEARCH_STUB) == \
        ActivityStatus.NOT_COLLECTABLE_VIA_CURRENT_METHOD
    assert classify.activity_status_from_text(
        "Accounts linked via Linktree", UrlKind.AGGREGATOR) == \
        ActivityStatus.NOT_COLLECTABLE_VIA_CURRENT_METHOD
    assert classify.activity_status_from_text(
        "Website active", UrlKind.HOMEPAGE) == ActivityStatus.ACTIVE_UNKNOWN_CADENCE
    assert classify.activity_status_from_text(
        "Candidate; current activity pending", UrlKind.HOMEPAGE) == \
        ActivityStatus.PENDING


def test_coverage_scope_mapping():
    assert classify.coverage_scope_from_text(
        "Statewide; Bilaspur trade relevance", fallback=CoverageScope.DISTRICT) == \
        CoverageScope.STATEWIDE
    assert classify.coverage_scope_from_text(
        "Bilaspur city", fallback=CoverageScope.DISTRICT) == CoverageScope.CITY
    assert classify.coverage_scope_from_text(
        "Bilaspur district", fallback=CoverageScope.CITY) == CoverageScope.DISTRICT
    assert classify.coverage_scope_from_text(
        "Chhattisgarh; Bilaspur coverage", fallback=CoverageScope.REGIONAL) == \
        CoverageScope.REGIONAL


def test_priority_mapping():
    assert classify.priority_for(SourceType.POLICE, "Police department") == Priority.P1
    assert classify.priority_for(SourceType.DISTRICT_GOVERNMENT, "District government") == \
        Priority.P1
    assert classify.priority_for(SourceType.PSU, "Public-sector company") == Priority.P2
    assert classify.priority_for(SourceType.CENTRAL_UNIVERSITY, "Central university") == \
        Priority.P2
    assert classify.priority_for(SourceType.PUBLISHER_LOCAL,
                                 "Bilaspur-focused publisher") == Priority.P1
    assert classify.priority_for(SourceType.LEAD, "Directory lead") == Priority.P3
    assert classify.priority_for(SourceType.AGGREGATOR, "") == Priority.P3
