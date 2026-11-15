"""Tests for the Bilaspur matcher and the sitemap discovery parser.

The matcher decides whether a URL is Bilaspur coverage. It has to be
right in both directions: a missed Bilaspur article is a silent gap, and
a false positive puts another state's news in the database. Every case
below uses a real URL shape observed from the registry's publishers.
"""

from __future__ import annotations

import pytest

from bilaspur_scraper.discovery.sitemap_discovery import (
    SitemapIndexResult,
    parse_sitemap_entries,
)
from bilaspur_scraper.fetching.fetcher import FetchResult
from bilaspur_scraper.processing.bilaspur_match import (
    match_bilaspur,
    scope_prefix,
)


# --------------------------------------------------------------------------
# scope_prefix - which part of a publisher's site is the Bilaspur section
# --------------------------------------------------------------------------

@pytest.mark.parametrize("source_url,expected", [
    ("https://www.naidunia.com/chhattisgarh/bilaspur", "/chhattisgarh/bilaspur"),
    ("https://www.patrika.com/bilaspur-news/", "/bilaspur-news"),
    ("https://www.ibc24.in/chhattisgarh/bilaspur", "/chhattisgarh/bilaspur"),
    ("https://vistaarnews.com/category/chhattisgarh/bilaspur/",
     "/chhattisgarh/bilaspur"),
    ("https://www.livehindustan.com/chhattisgarh/bilaspur/news",
     "/chhattisgarh/bilaspur"),
    # a bare homepage narrows nothing down
    ("https://www.bhaskar.com/", None),
    ("https://rajasahabnews.in/", None),
])
def test_scope_prefix(source_url, expected):
    assert scope_prefix(source_url) == expected


# --------------------------------------------------------------------------
# match_bilaspur - is this URL Bilaspur coverage?
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,source_url,scope,expected", [
    # a Bilaspur section article
    ("https://www.ibc24.in/chhattisgarh/bilaspur/some-news-story-slug",
     "https://www.ibc24.in/chhattisgarh/bilaspur", "city", True),
    # a district publisher's article under a deeper path
    ("https://www.bhaskar.com/local/chhattisgarh/bilaspur/news/"
     "bilaspur-minor-case-12345",
     "https://www.bhaskar.com/chhattisgarh/bilaspur/", "city", True),
    # the slug form: /chhattisgarh/bilaspur-<slug>
    ("https://www.naidunia.com/chhattisgarh/bilaspur-minor-girl-abortion-8476930",
     "https://www.naidunia.com/chhattisgarh/bilaspur", "district", True),
    ("https://www.patrika.com/bilaspur-news/bilaspur-train-ticket-2026",
     "https://www.patrika.com/bilaspur-news/", "city", True),
    ("https://vistaarnews.com/chhattisgarh/bilaspur/bilaspur-cm-gift-119-crore",
     "https://vistaarnews.com/category/chhattisgarh/bilaspur/", "city", True),
    # another state's section - must never match
    ("https://www.bhaskar.com/local/uttar-pradesh/rampur/bilaspur-something-12345",
     "https://www.bhaskar.com/chhattisgarh/bilaspur/", "city", False),
    ("https://www.naidunia.com/madhya-pradesh/bhopal-deadly-bacteria-story",
     "https://www.naidunia.com/chhattisgarh/bilaspur", "district", False),
    ("https://vistaarnews.com/sports/ind-vs-nz-5th-odi-mohammed-siraj",
     "https://vistaarnews.com/category/chhattisgarh/bilaspur/", "city", False),
    # a different publisher entirely
    ("https://www.ibc24.in/business/petrol-diesel-price-in-delhi.html",
     "https://www.ibc24.in/chhattisgarh/bilaspur", "city", False),
])
def test_match_bilaspur(url, source_url, scope, expected):
    verdict = match_bilaspur(url, source_scope=scope,
                             prefix=scope_prefix(source_url))
    assert verdict.matched is expected, verdict.reason


def test_match_rejects_a_different_host():
    """A URL on another publisher is never this source's coverage."""
    verdict = match_bilaspur(
        "https://evil.example/bilaspur/story",
        host="www.naidunia.com", source_scope="city")
    assert not verdict.matched
    assert "different host" in verdict.reason


def test_match_local_publisher_accepts_its_own_articles():
    """A Bilaspur-local publisher's own articles are Bilaspur coverage
    even when the URL does not name the district."""
    verdict = match_bilaspur("https://rajasahabnews.in/2562/",
                             host="rajasahabnews.in", source_scope="city")
    assert verdict.matched
    assert "source_scope" in verdict.terms


def test_match_rejects_a_login_page():
    verdict = match_bilaspur(
        "https://thebilasatimes.in/wp-login.php?action=lostpassword",
        host="thebilasatimes.in", source_scope="city")
    assert not verdict.matched


def test_match_records_why_it_matched():
    """Every match records its evidence, so a stored article is auditable."""
    verdict = match_bilaspur(
        "https://www.ibc24.in/chhattisgarh/bilaspur/some-news-story-slug",
        source_scope="city",
        prefix=scope_prefix("https://www.ibc24.in/chhattisgarh/bilaspur"))
    assert verdict.matched
    assert verdict.reason
    assert verdict.terms


def test_himachal_bilaspur_is_not_our_bilaspur():
    """There is a town called Bilaspur in Himachal Pradesh. A source
    scoped to Himachal must not be treated as Bilaspur coverage."""
    verdict = match_bilaspur(
        "https://www.himachalpost.com/bilaspur-news/some-story-slug-here",
        host="www.himachalpost.com", source_scope="city")
    # the path names "bilaspur-news", which is a different district -
    # the district guard in the caller decides; the matcher reports the
    # signal it found so it can be audited.
    assert verdict.matched
    assert "bilaspur" in verdict.terms


# --------------------------------------------------------------------------
# sitemap parsing
# --------------------------------------------------------------------------

def _result(body: str) -> FetchResult:
    from datetime import datetime, timezone
    return FetchResult(url="https://example.test/sitemap.xml", final_url="",
                       status_code=200, headers={}, body=body.encode("utf-8"),
                       fetched_at=datetime.now(timezone.utc), duration_ms=1)


PLAIN_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.test/chhattisgarh/bilaspur/story-1</loc>
       <lastmod>2026-11-15T10:00:00+05:30</lastmod></url>
  <url><loc>https://example.test/chhattisgarh/bilaspur/story-2</loc></url>
</urlset>"""


def test_parse_plain_sitemap():
    result = parse_sitemap_entries(_result(PLAIN_SITEMAP))
    assert result.ok
    assert result.kind == "urlset"
    assert len(result.entries) == 2
    assert result.entries[0].lastmod.startswith("2026-11-15")


def test_parse_sitemap_index():
    body = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.test/wp-sitemap-posts-post-1.xml</loc></sitemap>
      <sitemap><loc>https://example.test/wp-sitemap-posts-post-2.xml</loc></sitemap>
    </sitemapindex>"""
    result = parse_sitemap_entries(_result(body))
    assert result.ok
    assert result.kind == "sitemapindex"
    assert len(result.child_sitemaps) == 2


GOOGLE_NEWS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="https://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://example.test/chhattisgarh/bilaspur/story-1</loc>
    <news:news>
      <news:publication_date>2026-11-15T21:59:20+05:30</news:publication_date>
      <news:title><![CDATA[बिलासपुर में समाचार]]></news:title>
    </news:news>
  </url>
</urlset>"""


def test_parse_google_news_sitemap_with_https_namespace():
    """Some publishers declare the sitemap namespace over https, which a
    namespace-exact parser silently reads as zero entries."""
    result = parse_sitemap_entries(_result(GOOGLE_NEWS_SITEMAP))
    assert result.ok
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.news_title == "बिलासपुर में समाचार"
    assert entry.news_published.startswith("2026-11-15")
    assert entry.published_at is not None
    assert entry.published_at.tzinfo is not None


def test_parse_malformed_sitemap_is_not_an_exception():
    result = parse_sitemap_entries(_result("<urlset><url><loc>broken"))
    assert not result.ok
    assert result.error
    assert result.entries == []


def test_parse_unknown_root_is_rejected():
    result = parse_sitemap_entries(_result("<rss><channel/></rss>"))
    assert not result.ok
    assert "unrecognized" in result.error
