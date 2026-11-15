"""Canonical enums for the Bilaspur News Scraper.

Every controlled vocabulary lives here exactly once (`SKILLS.md` §14:
typed models and explicit enums). Values follow `PLAN.md` §5 and
`ARCHITECTURE.md` §4/§10.
"""

from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    """Source lifecycle state machine (`ARCHITECTURE.md` §4)."""

    CANDIDATE = "candidate"
    DISCOVERED = "discovered"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"
    ENABLED = "enabled"
    DISABLED = "disabled"
    REJECTED = "rejected"
    EXCLUDED = "excluded"


class SourceType(str, Enum):
    DISTRICT_GOVERNMENT = "district_government"
    POLICE = "police"
    ELECTION_OFFICE = "election_office"
    PSU = "psu"
    CENTRAL_UNIVERSITY = "central_university"
    STATE_UNIVERSITY = "state_university"
    URBAN_LOCAL_BODY = "urban_local_body"
    DISTRICT_LOCAL_GOVERNMENT = "district_local_government"
    JUDICIARY = "judiciary"
    INDUSTRY_ASSOCIATION = "industry_association"
    TRADE_ASSOCIATION = "trade_association"
    STATE_GOVERNMENT = "state_government"
    PUBLISHER_LOCAL = "publisher_local"
    PUBLISHER_REGIONAL = "publisher_regional"
    PUBLISHER_NATIONAL = "publisher_national"
    BROADCASTER = "broadcaster"
    AGGREGATOR = "aggregator"
    LEAD = "lead"


class Platform(str, Enum):
    WEBSITE = "website"
    RSS = "rss"
    # Reserved for deferred phases (social platforms are out of scope in v1).
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    X = "x"


class UrlKind(str, Enum):
    HOMEPAGE = "homepage"
    SECTION = "section"
    TOPIC = "topic"
    VIDEO_SECTION = "video_section"
    SUB_PATH = "sub_path"
    AGGREGATOR = "aggregator"
    SEARCH_STUB = "search_stub"


class CoverageScope(str, Enum):
    CITY = "city"
    DISTRICT = "district"
    NEARBY = "nearby"
    STATEWIDE = "statewide"
    REGIONAL = "regional"
    NATIONAL = "national"


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class CollectionMethod(str, Enum):
    RSS = "rss"
    SITEMAP = "sitemap"
    HTML = "html"
    JSON_LD = "json_ld"
    API = "api"
    NONE = "none"


class SourceVerificationStatus(str, Enum):
    VERIFIED_OFFICIAL = "verified_official"
    PUBLISHER_LINKED = "publisher_linked"
    PORTAL_LINKED = "portal_linked"
    SEARCH_LINK_ONLY = "search_link_only"
    UNVERIFIED = "unverified"


class ActivityStatus(str, Enum):
    ACTIVE = "active"
    ACTIVE_UNKNOWN_CADENCE = "active_unknown_cadence"
    PENDING = "pending"
    BLOCKED = "blocked"
    PARSER_BROKEN = "parser_broken"
    INACTIVE = "inactive"
    NOT_COLLECTABLE_VIA_CURRENT_METHOD = "not_collectable_via_current_method"


class VerificationMethod(str, Enum):
    RSS = "rss"
    SITEMAP = "sitemap"
    LISTING = "listing"
    MANUAL = "manual"


class RecordStatus(str, Enum):
    COLLECTED = "collected"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    EXTRACTION_FAILED = "extraction_failed"
    REJECTED = "rejected"


class ErrorCode(str, Enum):
    """Canonical error taxonomy - exactly one category per failure
    (`ARCHITECTURE.md` §10)."""

    E_CONFIG = "E_CONFIG"
    E_POLICY = "E_POLICY"
    E_ROBOTS_UNAVAILABLE = "E_ROBOTS_UNAVAILABLE"
    E_DNS = "E_DNS"
    E_CONNECT = "E_CONNECT"
    E_TLS = "E_TLS"
    E_HTTP_CLIENT = "E_HTTP_CLIENT"
    E_RATE_LIMITED = "E_RATE_LIMITED"
    E_HTTP_SERVER = "E_HTTP_SERVER"
    E_TIMEOUT = "E_TIMEOUT"
    E_BUDGET = "E_BUDGET"
    E_PARSE = "E_PARSE"
    E_VALIDATION = "E_VALIDATION"
    E_STORAGE = "E_STORAGE"


#: Verification policy version (`ARCHITECTURE.md` D17). Increment whenever
#: the verification policy or its budget changes.
VERIFICATION_POLICY_VERSION = "v1"

#: Hard cap of outbound requests per verification attempt (`ARCHITECTURE.md` D9).
VERIFICATION_REQUEST_BUDGET = 10
