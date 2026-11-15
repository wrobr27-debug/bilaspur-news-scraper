"""Sitemap parsing (XML urlset / sitemapindex) (`ARCHITECTURE.md` §12)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from ..fetching.fetcher import FetchResult

logger = logging.getLogger(__name__)

_NS_URLSET = "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
_NS_SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap"


@dataclass
class SitemapResult:
    ok: bool
    kind: str  # 'urlset' | 'sitemapindex' | 'none'
    urls: list[str]
    child_sitemaps: list[str]
    lastmod_by_url: dict[str, str]
    error: str = ""


def _loc_text(elem) -> Optional[str]:
    loc = elem.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
    if loc is None or not (loc.text or "").strip():
        return None
    return loc.text.strip()


def parse_sitemap(result: FetchResult) -> SitemapResult:
    body = result.body or b""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return SitemapResult(ok=False, kind="none", urls=[], child_sitemaps=[],
                             lastmod_by_url={}, error=f"malformed sitemap XML: {exc}")
    urls: list[str] = []
    children: list[str] = []
    lastmod: dict[str, str] = {}
    if root.tag == _NS_URLSET or root.tag.endswith("}urlset") or root.tag == "urlset":
        for url_elem in root:
            if not url_elem.tag.endswith("url"):
                continue
            loc = _loc_text(url_elem)
            if not loc:
                continue
            urls.append(loc)
            mod = url_elem.find("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")
            if mod is not None and (mod.text or "").strip():
                lastmod[loc] = mod.text.strip()
        return SitemapResult(ok=True, kind="urlset", urls=urls,
                             child_sitemaps=[], lastmod_by_url=lastmod)
    if root.tag == _NS_SITEMAP or root.tag.endswith("}sitemapindex") \
            or root.tag == "sitemapindex":
        for sm_elem in root:
            if not sm_elem.tag.endswith("sitemap"):
                continue
            loc = _loc_text(sm_elem)
            if loc:
                children.append(loc)
        return SitemapResult(ok=True, kind="sitemapindex", urls=[],
                             child_sitemaps=children, lastmod_by_url={})
    return SitemapResult(ok=False, kind="none", urls=[], child_sitemaps=[],
                         lastmod_by_url={}, error="unrecognized sitemap root")
