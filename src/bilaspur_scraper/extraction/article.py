"""Extraction - layered fill-missing (`ARCHITECTURE.md` §13, D14).

Order: structured metadata (JSON-LD / Open Graph / canonical) ->
semantic DOM -> registered handler (none yet in v1) -> trafilatura
fallback. Later layers fill gaps only; they never overwrite stronger
data. A confidence gate applies to the final draft. Nothing is guessed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import trafilatura
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ..fetching.fetcher import FetchResult

logger = logging.getLogger(__name__)

MIN_BODY_CHARS = 300
JSON_LD_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting",
                 "WebPage"}


@dataclass
class ExtractionOutput:
    fields: dict = field(default_factory=dict)
    parser_name: str = "generic-html"
    confidence_ok: bool = False
    failure_reason: str = ""


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _parse_date(raw: str) -> tuple[Optional[object], str]:
    """(published_at | published_date, precision) - never guesses tz."""
    raw = _clean(raw) or ""
    try:
        parsed = date_parser.isoparse(raw)
    except (ValueError, TypeError):
        try:
            parsed = date_parser.parse(raw, dayfirst=False)
        except (ValueError, TypeError, OverflowError):
            return None, "unknown"
    if len(raw) == 10:  # YYYY-MM-DD -> date-only, never invent a time
        return parsed.date(), "date-only"
    if parsed.tzinfo is not None:
        return parsed, "timestamp"
    # Naive datetime: do not assume a timezone -> keep date only.
    return parsed.date(), "date-only"


def structured_metadata(soup: BeautifulSoup, base_url: str) -> dict:
    """JSON-LD -> Open Graph -> canonical link. Strongest layer."""
    out: dict = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and "@graph" in data:
            candidates.extend(data["@graph"])
        for node in candidates:
            if not isinstance(node, dict):
                continue
            types = node.get("@type", [])
            types = {types} if isinstance(types, str) else set(types)
            if not types & JSON_LD_TYPES:
                continue
            out.setdefault("title", _clean(node.get("headline") or node.get("name")))
            out.setdefault("author", _clean_author(node.get("author")))
            out.setdefault("featured_image_url", _clean_image(node.get("image")))
            date_raw = node.get("datePublished")
            if date_raw and "published" not in out:
                value, precision = _parse_date(date_raw)
                if value is not None:
                    out["published"] = value
                    out["published_time_precision"] = precision
                    out["published_at_original"] = _clean(date_raw)
            modified = node.get("dateModified")
            if modified and "updated" not in out:
                value, _ = _parse_date(modified)
                if value is not None:
                    out["updated"] = value
    og_map = {
        "og:title": "title",
        "og:image": "featured_image_url",
        "article:published_time": "og_published",
        "article:modified_time": "og_updated",
    }
    for prop, key in og_map.items():
        tag = soup.find("meta", attrs={"property": prop})
        if tag and (tag.get("content") or "").strip() and key not in out:
            if key in ("og_published", "og_updated"):
                value, precision = _parse_date(tag["content"])
                if value is not None:
                    out.setdefault("published" if key == "og_published" else "updated",
                                   value)
                    out.setdefault("published_time_precision", precision)
                    out.setdefault("published_at_original", _clean(tag["content"]))
            else:
                out.setdefault(key, _clean(tag.get("content")))
    canonical = soup.find("link", rel=lambda r: r and "canonical" in r)
    if canonical and canonical.get("href"):
        from urllib.parse import urljoin
        out.setdefault("canonical_url", urljoin(base_url, canonical["href"].strip()))
    return out


def _clean_author(author) -> Optional[str]:
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        author = author.get("name")
    return _clean(author)


def _clean_image(image) -> Optional[str]:
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    return _clean(image)


def semantic_dom(soup: BeautifulSoup) -> dict:
    """<article>, headings, <time>, author markup."""
    out: dict = {}
    article_tag = soup.find("article") or soup.find(
        attrs={"itemprop": "articleBody"}) or soup.find(
        attrs={"class": re_compile_article_body()})
    if article_tag is not None:
        paragraphs = [p.get_text(" ", strip=True) for p in article_tag.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        if paragraphs:
            out.setdefault("body", "\n\n".join(paragraphs))
    if soup.h1 and soup.h1.get_text(strip=True):
        out.setdefault("title", _clean(soup.h1.get_text(strip=True)))
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag is not None:
        value, precision = _parse_date(time_tag["datetime"])
        if value is not None:
            out.setdefault("published", value)
            out.setdefault("published_time_precision", precision)
            out.setdefault("published_at_original", _clean(time_tag["datetime"]))
    if not out.get("published"):
        meta = soup.find("meta", attrs={"name": re.compile(
            r"^(publish-date|pubdate|dc.date|DC.date.issued|date)$", re.I)})
        if meta and (meta.get("content") or "").strip():
            value, precision = _parse_date(meta["content"])
            if value is not None:
                out["published"] = value
                out["published_time_precision"] = precision
                out["published_at_original"] = _clean(meta["content"])
    for selector in ({"rel": "author"}, {"class": re.compile(r"author|byline", re.I)},
                     {"itemprop": "author"}):
        tag = soup.find(attrs=selector)
        if tag is not None:
            text = _clean(tag.get_text(" ", strip=True))
            if text and len(text) < 120:
                out.setdefault("author", text)
                break
    return out


def re_compile_article_body():
    import re
    return re.compile(r"(article-body|entry-content|post-content|story-body|"
                      r"articleBody|content-body)", re.I)


def trafilatura_fallback(html: str, url: str) -> dict:
    """Main-text fallback + metadata. Weakest layer - fills gaps only."""
    out: dict = {}
    text = trafilatura.extract(html, url=url, include_comments=False,
                               include_tables=False, favor_recall=True)
    if text:
        out["body"] = text
    try:
        meta = trafilatura.extract_metadata(html=html, url=url)
    except Exception:  # noqa: BLE001 - metadata is best-effort
        meta = None
    if meta is not None:
        if getattr(meta, "title", None) and "title" not in out:
            out["title"] = _clean(meta.title)
        if getattr(meta, "author", None) and "author" not in out:
            out["author"] = _clean(meta.author)
        if getattr(meta, "date", None) and "published" not in out:
            value, precision = _parse_date(meta.date)
            if value is not None:
                out["published"] = value
                out["published_time_precision"] = precision
                out["published_at_original"] = _clean(meta.date)
    return out


def extract_article(result: FetchResult, source_id: str) -> ExtractionOutput:
    """Layered fill-missing extraction from one fetched article page."""
    html = result.text()
    if not html.strip():
        return ExtractionOutput(failure_reason="empty body")
    soup = BeautifulSoup(html, "html.parser")
    fields: dict = {"source_id": source_id, "original_url": result.url,
                    "extraction_method": "html"}

    structured = structured_metadata(soup, result.final_url)
    dom = semantic_dom(soup)

    if structured.get("title") and len(structured["title"]) > 5:
        fields["title"] = structured["title"]
        fields["extraction_method"] = "json_ld"
    elif dom.get("title") and len(dom["title"]) > 5:
        fields["title"] = dom["title"]

    # Published date: structured wins; semantic fills only if absent/weak.
    published = structured.get("published") or dom.get("published")
    if published is not None:
        fields["published"] = published
    precision = structured.get("published_time_precision") \
        or dom.get("published_time_precision") or "unknown"
    fields["published_time_precision"] = precision
    original = structured.get("published_at_original") \
        or dom.get("published_at_original")
    if original:
        fields["published_at_original"] = original

    body = dom.get("body")
    if body and len(body) >= MIN_BODY_CHARS:
        fields["body"] = body
    fallback = trafilatura_fallback(html, result.final_url)
    if not fields.get("body") or len(fields.get("body", "")) < MIN_BODY_CHARS:
        if fallback.get("body") and len(fallback["body"]) > len(
                fields.get("body", "")):
            fields["body"] = fallback["body"]
            fields["extraction_method"] = "trafilatura"
    if not fields.get("title") and fallback.get("title"):
        fields["title"] = fallback["title"]
    if not fields.get("published"):
        if fallback.get("published") is not None:
            fields["published"] = fallback["published"]
            fields.setdefault("published_time_precision",
                              fallback.get("published_time_precision", "unknown"))
            fields.setdefault("published_at_original", fallback.get("published_at_original"))

    author = structured.get("author") or dom.get("author") or fallback.get("author")
    if author:
        fields["author"] = author
    canonical = structured.get("canonical_url")
    if canonical:
        fields["canonical_url"] = canonical
    image = structured.get("featured_image_url")
    if image:
        fields["featured_image_url"] = image

    body_text = fields.get("body") or ""
    title = fields.get("title") or ""
    # Confidence gate on the final draft.
    if not title or len(title) < 8:
        return ExtractionOutput(fields=fields, confidence_ok=False,
                                failure_reason="no confident title")
    if len(body_text) < MIN_BODY_CHARS:
        return ExtractionOutput(fields=fields, confidence_ok=False,
                                failure_reason=f"body too short "
                                               f"({len(body_text)} chars)")
    lowered = body_text[:400].lower()
    if any(phrase in lowered for phrase in ("page not found", "404 error",
                                            "access denied", "access forbidden")):
        return ExtractionOutput(fields=fields, confidence_ok=False,
                                failure_reason="error page content")
    return ExtractionOutput(fields=fields, parser_name="generic-html/1",
                            confidence_ok=True)
