"""Social / video source tables (`SKILLS.md` §4.2, §11).

These sit beside the website registry rather than inside it. A YouTube
channel, an Instagram page and a publisher website are different kinds
of source with different collection methods, different permission
grounds and different rights, so they get their own tables.

Nothing here downloads media. The tables record *what exists* and where
it is, so that a later, separately authorised step can decide what to
collect.

Tables:
    social_accounts     one row per platform account (YouTube, Instagram,
                        Facebook, X, ...)
    social_videos       one row per discovered video, with the evidence
                        that made it a Bilaspur candidate
    media_captions      the transcript of a video, once obtained
    media_transcripts   the result of speech-to-text over audio
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow


class SocialAccount(Base):
    """A platform account that may publish Bilaspur-relevant content.

    `verification_status` mirrors the website registry's vocabulary so
    the two can be filtered together:

        verified_official   linked from an official site, or an
                            institution's own channel
        publisher_linked    a publisher's account, linked from its site
        account_identified  found, but the link is not yet confirmed
        search_link_only    only a search URL is known - not a profile
        unverified          nothing confirms ownership
        excluded            not usable (duplicate, impostor, inactive)

    `collectable_via` names the permitted collection method. It is empty
    when no permitted method exists, which means the account is a lead
    for a human, not something to crawl.
    """

    __tablename__ = "social_accounts"

    account_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    account_name: Mapped[str] = mapped_column(String(200))
    handle: Mapped[str | None] = mapped_column(String(160), index=True)
    profile_url: Mapped[str] = mapped_column(Text)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)

    account_type: Mapped[str] = mapped_column(String(48), default="unknown")
    owner_entity: Mapped[str | None] = mapped_column(String(200))
    coverage_scope: Mapped[str | None] = mapped_column(String(32))
    relevance: Mapped[str | None] = mapped_column(String(24))

    verification_status: Mapped[str] = mapped_column(
        String(32), default="unverified", index=True)
    ownership_evidence: Mapped[str | None] = mapped_column(Text)
    collectable_via: Mapped[str | None] = mapped_column(String(64))
    discovery_method: Mapped[str] = mapped_column(String(48),
                                                  default="manual")
    discovery_evidence: Mapped[str | None] = mapped_column(Text)

    subscribers: Mapped[str | None] = mapped_column(String(32))
    video_count: Mapped[str | None] = mapped_column(String(32))
    last_posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False,
                                              index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    origin_sheet: Mapped[str | None] = mapped_column(String(64))
    origin_row: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SocialVideo(Base):
    """A video discovered on a platform account.

    `match_evidence` records *why* the video was treated as a Bilaspur
    candidate - the matched term and the query that surfaced it - so a
    later reviewer can see the basis for the classification.
    """

    __tablename__ = "social_videos"
    __table_args__ = (
        UniqueConstraint("platform", "video_id", name="uq_social_video"),
        Index("ix_social_video_account", "account_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                     autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("social_accounts.account_id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    published_label: Mapped[str | None] = mapped_column(String(48))
    views_label: Mapped[str | None] = mapped_column(String(32))
    match_evidence: Mapped[str | None] = mapped_column(Text)
    match_terms: Mapped[str | None] = mapped_column(Text)
    discovered_via: Mapped[str] = mapped_column(String(64), default="search")
    discovery_query: Mapped[str | None] = mapped_column(String(160))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    default=utcnow)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   default=utcnow)


class MediaTranscript(Base):
    """The transcript of a video, and how it was obtained.

    `method` distinguishes a transcript the publisher already supplied
    (YouTube captions) from one we generated with speech-to-text over
    downloaded audio. The distinction matters: a publisher's own
    caption track is their text; a generated one is our transcription of
    their audio.
    """

    __tablename__ = "media_transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                     autoincrement=True)
    video_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_videos.id"), index=True)
    method: Mapped[str] = mapped_column(String(32), index=True)
    language: Mapped[str | None] = mapped_column(String(16))
    language_source: Mapped[str | None] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    segment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(96))
    model_version: Mapped[str | None] = mapped_column(String(48))
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)
