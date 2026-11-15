"""Layered extraction with fixture HTML (offline; D14 confidence gate)."""

from __future__ import annotations

from datetime import datetime, timezone

from bilaspur_scraper.extraction.article import extract_article
from bilaspur_scraper.fetching.fetcher import FetchResult

PAGE = """<!doctype html>
<html><head>
<title>Small title</title>
<link rel="canonical" href="https://example.org/news/canonical-1">
<script type="application/ld+json">
{"@type": "NewsArticle", "headline": "Bilaspur civic drive begins today",
 "author": {"name": "Staff Reporter"}, "datePublished": "2026-01-05T10:30:00+05:30",
 "image": "https://example.org/img/1.jpg"}
</script>
<meta property="og:image" content="https://example.org/img/og.jpg">
</head>
<body>
<article>
<h1>Unused heading</h1>
<p> Paragraph one of the story body with plenty of detail about the drive. </p>
<p> Paragraph two continues the report with district administration quotes
and public reaction from across the city wards and localities. </p>
<p> Paragraph three adds closing detail so that the body comfortably passes
the three hundred character confidence gate required by policy. </p>
</article>
</body></html>"""

SHORT_BODY_PAGE = """<html><head><title>OK</title></head>
<body><article><h1>A perfectly fine headline here</h1>
<p>Too short.</p></article></body></html>"""

NO_TITLE_PAGE = """<html><body><div class="entry-content">
<p>""" + ("Long enough body text. " * 30) + """</p></div></body></html>"""


def _result(url: str, page: str) -> FetchResult:
    return FetchResult(url=url, final_url=url, status_code=200,
                       headers={"Content-Type": "text/html; charset=utf-8"},
                       body=page.encode("utf-8"),
                       fetched_at=datetime.now(timezone.utc), duration_ms=1)


def test_json_ld_fills_title_author_date():
    out = extract_article(_result("https://example.org/news/1", PAGE), "s1")
    assert out.confidence_ok
    assert out.fields["title"] == "Bilaspur civic drive begins today"
    assert out.fields["author"] == "Staff Reporter"
    assert out.fields["published_time_precision"] == "timestamp"
    assert out.fields["published_at_original"] == "2026-01-05T10:30:00+05:30"
    assert out.fields["canonical_url"] == "https://example.org/news/canonical-1"
    assert out.fields["featured_image_url"] == "https://example.org/img/1.jpg"
    assert out.fields["extraction_method"] == "json_ld"


def test_confidence_gate_rejects_short_body():
    out = extract_article(_result("https://example.org/news/2", SHORT_BODY_PAGE), "s1")
    assert not out.confidence_ok
    assert "body too short" in out.failure_reason


def test_missing_title_fails_gate():
    out = extract_article(_result("https://example.org/news/3", NO_TITLE_PAGE), "s1")
    assert not out.confidence_ok
    assert "title" in out.failure_reason


def test_empty_body_fails_cleanly():
    out = extract_article(_result("https://example.org/news/4", "<html></html>"), "s1")
    assert not out.confidence_ok
