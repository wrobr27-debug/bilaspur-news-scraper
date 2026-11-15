"""URL normalization and content hashing (dedup signals 1-3)."""

from __future__ import annotations

from bilaspur_scraper.processing.dedup import content_hash, normalize_url


def test_normalize_url_strips_tracking_params():
    assert normalize_url(
        "https://Example.com/News/Story/?utm_source=fb&id=7") == \
        "https://example.com/News/Story?id=7"


def test_normalize_url_drops_fragment_and_trailing_slash():
    assert normalize_url("https://example.com/a/b/") == "https://example.com/a/b"
    assert normalize_url("https://example.com/a/b#top") == "https://example.com/a/b"


def test_normalize_url_is_stable():
    variants = [
        "https://example.com/news/x?utm_campaign=t",
        "https://EXAMPLE.com/news/x",
        "https://example.com/news/x/",
    ]
    assert len({normalize_url(v) for v in variants}) == 1


def test_content_hash_is_versioned_and_stable():
    h1 = content_hash("Bilaspur  drive  begins", "Body text.  ",
                      "2026-01-05")
    h2 = content_hash("bilaspur drive begins", "body text.", "2026-01-05")
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_content_hash_changes_with_date():
    a = content_hash("title", "body", "2026-01-05")
    b = content_hash("title", "body", "2026-01-06")
    assert a != b
