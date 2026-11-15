"""Canonical article contracts (`ARCHITECTURE.md` §7.3, §7.4).

`ArticleDraft` is the single output of every parser (D6). Processing
fills relevance/dedup fields to produce the validated `ArticleRecord`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

LanguageCode = Literal["hi", "en", "und"]
RelevanceScore = Literal["high", "medium", "low", "unknown"]
TimePrecision = Literal["date-only", "timestamp", "unknown"]
RecordStatusValue = Literal["collected", "needs_review", "duplicate",
                            "extraction_failed", "rejected"]
VerificationStatusValue = Literal["verified_official", "publisher_linked",
                                  "portal_linked", "search_link_only", "unverified"]
ContentType = Literal["article", "press_release", "notice", "advisory", "video",
                      "public_post", "event", "other"]


class ArticleDraft(BaseModel):
    """Extraction-layer output. Missing fields stay None - never guessed."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    original_url: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    content_text: Optional[str] = None
    author: Optional[str] = None
    language: LanguageCode = "und"
    published_at: Optional[datetime] = None
    published_date: Optional[date] = None
    published_time_precision: TimePrecision = "unknown"
    published_at_original: Optional[str] = None
    content_type: ContentType = "article"
    location_mentions: list[str] = Field(default_factory=list)
    extraction_method: str = "html"
    parser_name: str = ""
    parser_version: str = "1"
    featured_image_url: Optional[str] = None
    summary_from_source: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class ArticleRecord(BaseModel):
    """Validated record ready for persistence (`ARCHITECTURE.md` §7.4)."""

    model_config = ConfigDict(frozen=True)

    draft: ArticleDraft
    bilaspur_relevance: RelevanceScore
    relevance_reason: str
    matched_terms: list[str] = Field(default_factory=list)
    sensitive: bool = False
    content_hash: str
    record_status: RecordStatusValue
    source_verification_status: VerificationStatusValue
    retrieved_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
