"""Tests for the social / video dataset.

Two things matter most here and are easy to get wrong:

* an account must not be treated as confirmed when its URL is only a
  search-results URL - that would put an unverified handle into a
  collection queue;
* the verification status must land in the canonical vocabulary, because
  the website registry and the social registry are filtered together.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from build_social_dataset import (  # noqa: E402
    account_id_for,
    is_direct_profile,
    normalise_verification,
)


@pytest.mark.parametrize("platform,url,expected", [
    ("youtube", "https://www.youtube.com/@takhatpurkibhadas-tr3mo", True),
    ("youtube", "https://www.youtube.com/channel/UCUKbgNXlI1vvidm4ouqTPSw", True),
    ("youtube", "https://www.youtube.com/results?search_query=IBC24", False),
    ("instagram", "https://www.instagram.com/seclbilaspur/", True),
    ("instagram", "https://www.instagram.com/explore/search/keyword/?q=ibc24",
     False),
    ("facebook", "https://www.facebook.com/southeasterncoalfields", True),
    ("facebook", "https://www.facebook.com/search/pages/?q=The%20Bilasa", False),
    ("x", "https://x.com/bsppolicecg", True),
    ("x", "https://twitter.com/search?q=DEO%20Bilaspur", False),
])
def test_is_direct_profile(platform, url, expected):
    assert is_direct_profile(platform, url) is expected


@pytest.mark.parametrize("text,is_direct,expected", [
    ("Publisher-linked", True, "publisher_linked"),
    ("Publisher-linked; activity pending", True, "publisher_linked"),
    ("Official-site linked", True, "verified_official"),
    ("Official account identified", True, "verified_official"),
    ("Company-listed", True, "verified_official"),
    ("Account identified", True, "account_identified"),
    ("Exact channel pending", True, "account_identified"),
    ("Search/discovery URL only", True, "search_link_only"),
    ("Official link hub", True, "search_link_only"),
    ("Unverified", True, "unverified"),
    ("", True, "unverified"),
    (None, True, "unverified"),
    # a claimed status on a search URL cannot stand
    ("Publisher-linked", False, "search_link_only"),
    ("Official-site linked", False, "search_link_only"),
    ("Account identified", False, "search_link_only"),
])
def test_normalise_verification(text, is_direct, expected):
    assert normalise_verification(text, is_direct=is_direct) == expected


def test_account_id_is_stable_and_unique():
    a = account_id_for("youtube", "The Bilasa Times",
                        "https://www.youtube.com/channel/UCUKbgNXlI1vvidm4ouqTPSw")
    b = account_id_for("youtube", "The Bilasa Times",
                        "https://www.youtube.com/channel/UCUKbgNXlI1vvidm4ouqTPSw")
    assert a == b
    # a different URL for the same name must not collide
    c = account_id_for("youtube", "News",
                        "https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaa")
    d = account_id_for("youtube", "News",
                        "https://www.youtube.com/channel/UCbbbbbbbbbbbbbbbbbbbbb")
    assert c != d


def test_dataset_tables_exist(tmp_path):
    """The media tables are created by `create_schema`."""
    from bilaspur_scraper.database.media_models import (
        MediaTranscript,
        SocialAccount,
        SocialVideo,
    )
    from bilaspur_scraper.database.session import (
        create_db_engine,
        create_schema,
        make_session_factory,
    )

    db = tmp_path / "media.db"
    engine = create_db_engine(f"sqlite:///{db}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(SocialAccount(
            account_id="youtube-test", platform="youtube",
            account_name="Test", profile_url="https://www.youtube.com/@test",
            verification_status="account_identified"))
        session.add(SocialVideo(
            account_id="youtube-test", platform="youtube", video_id="abc12345678",
            url="https://www.youtube.com/watch?v=abc12345678",
            title="बिलासपुर समाचार", match_terms="bilaspur"))
        session.commit()

    with factory() as session:
        assert session.query(SocialAccount).count() == 1
        assert session.query(SocialVideo).count() == 1
        assert session.query(MediaTranscript).count() == 0
    engine.dispose()


def test_social_video_video_id_is_unique(tmp_path):
    """The same video must not be stored twice."""
    from sqlalchemy.exc import IntegrityError

    from bilaspur_scraper.database.media_models import SocialAccount, SocialVideo
    from bilaspur_scraper.database.session import (
        create_db_engine,
        create_schema,
        make_session_factory,
    )

    db = tmp_path / "media2.db"
    engine = create_db_engine(f"sqlite:///{db}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        for aid in ("a", "b"):
            session.add(SocialAccount(
                account_id=aid, platform="youtube", account_name=aid,
                profile_url=f"https://www.youtube.com/@{aid}"))
        session.commit()
    with factory() as session:
        session.add(SocialVideo(
            account_id="a", platform="youtube", video_id="dup12345678",
            url="https://www.youtube.com/watch?v=dup12345678"))
        session.commit()
    with factory() as session:
        session.add(SocialVideo(
            account_id="b", platform="youtube", video_id="dup12345678",
            url="https://www.youtube.com/watch?v=dup12345678"))
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()
