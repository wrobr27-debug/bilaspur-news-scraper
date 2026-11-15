"""Feed parsing and sitemap parsing with fixture payloads (offline)."""

from __future__ import annotations

from datetime import datetime, timezone

from bilaspur_scraper.discovery.feeds import parse_feed
from bilaspur_scraper.discovery.sitemaps import parse_sitemap
from bilaspur_scraper.fetching.fetcher import FetchResult

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Jila Prashasan</title>
<link>https://example.org/</link>
<description>t</description>
<item><title>Bilaspur meet held</title>
<link>https://example.org/news/meet-1</link>
<pubDate>Mon, 05 Jan 2026 10:00:00 +0000</pubDate>
<description>Summary one</description>
<category>Civic</category></item>
<item><title>Second item</title>
<link>https://example.org/news/meet-2</link>
<pubDate>Tue, 06 Jan 2026 11:30:00 +0000</pubDate>
</item>
</channel></rss>"""

ATOM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>t</title><link href="https://example.org/"/><updated>2026-01-05T00:00:00Z</updated>
<entry><title>Atom entry</title>
<link href="https://example.org/news/atom-1"/>
<updated>2026-01-05T09:00:00Z</updated>
<summary>Atom summary</summary></entry></feed>"""

MALFORMED_XML = b"<rss><channel><item><link>x"

URLSET_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.org/news/a</loc><lastmod>2026-01-05</lastmod></url>
<url><loc>https://example.org/news/b</loc></url>
</urlset>"""

INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://example.org/sitemap-news.xml</loc></sitemap>
</sitemapindex>"""


def _result(url: str, body: bytes, content_type="application/xml") -> FetchResult:
    return FetchResult(url=url, final_url=url, status_code=200,
                       headers={"Content-Type": content_type}, body=body,
                       fetched_at=datetime.now(timezone.utc), duration_ms=1)


def test_parse_rss_feed_entries():
    parsed = parse_feed(_result("https://example.org/rss.xml", RSS_XML))
    assert parsed.ok
    assert parsed.feed_title == "Jila Prashasan"
    assert len(parsed.entries) == 2
    first = parsed.entries[0]
    assert first.url == "https://example.org/news/meet-1"
    assert first.published_at is not None
    assert first.published_at.year == 2026
    assert first.tags == ["Civic"]
    assert first.summary == "Summary one"


def test_parse_atom_feed_entries():
    parsed = parse_feed(_result("https://example.org/atom.xml", ATOM_XML))
    assert parsed.ok
    assert len(parsed.entries) == 1
    assert parsed.entries[0].url == "https://example.org/news/atom-1"
    assert parsed.entries[0].published_at is not None


def test_malformed_feed_is_not_ok():
    parsed = parse_feed(_result("https://example.org/rss.xml", MALFORMED_XML))
    assert not parsed.ok
    assert parsed.error


def test_empty_feed_is_not_ok():
    parsed = parse_feed(_result("https://example.org/rss.xml",
                                b"<rss version='2.0'><channel/></rss>"))
    assert not parsed.ok or not parsed.entries


def test_parse_urlset_sitemap():
    parsed = parse_sitemap(_result("https://example.org/sitemap.xml", URLSET_XML))
    assert parsed.ok and parsed.kind == "urlset"
    assert parsed.urls == ["https://example.org/news/a",
                           "https://example.org/news/b"]
    assert parsed.lastmod_by_url["https://example.org/news/a"] == "2026-01-05"


def test_parse_sitemapindex():
    parsed = parse_sitemap(_result("https://example.org/sitemap.xml", INDEX_XML))
    assert parsed.ok and parsed.kind == "sitemapindex"
    assert parsed.child_sitemaps == ["https://example.org/sitemap-news.xml"]


def test_garbage_sitemap_is_not_ok():
    parsed = parse_sitemap(_result("https://example.org/sitemap.xml", b"not xml <"))
    assert not parsed.ok
