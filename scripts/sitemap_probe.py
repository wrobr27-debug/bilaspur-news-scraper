"""Test whether sitemap-discovered articles extract over plain HTTP.

For each source with a sitemap, this:
  1. fetches the sitemap (following a sitemap index if needed)
  2. picks the newest entries whose URL contains a Bilaspur-related term
  3. fetches ONE article page and runs the real extraction pipeline
  4. reports status, body length, and whether the confidence gate passed

That answers the only question that matters for the architecture: which
sources need the browser at all?

Usage:
    python scripts/sitemap_probe.py            # test the sources with sitemaps
    python scripts/sitemap_probe.py --all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.config.settings import load_settings  # noqa: E402
from bilaspur_scraper.extraction.article import extract_article  # noqa: E402
from bilaspur_scraper.fetching.fetcher import FetchManager  # noqa: E402

ENDPOINTS = ROOT / "data" / "endpoints.json"

#: A URL must mention one of these to count as Bilaspur-relevant.
BILASPUR_TERMS = (
    "bilaspur", "बिलासपुर", "bilaspur-", "bemetara", "behatar",
    "kota", "तखतपुर", "takhatpur", "ratanpur", "ratnpur", "bilaspur",
    "mastar", "masturi", "sarkanda", "sarkanda", "koni", "कौनी",
    "kotent", "gadarwara", "bilaspur", "pandaria", "pendra", "pendra",
    "champa", "sakti", "lormi", "mungeli", "manendragarh",
)

LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>")
LASTMOD_RE = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.IGNORECASE)


def _fetch(fetcher, url: str, timeout: int):
    """Fetch one URL and return the real `FetchResult` (the extraction
    pipeline needs the object, not a plain dict)."""
    import asyncio

    async def _one():
        try:
            fetcher._settings = type(fetcher._settings)(
                **{**fetcher._settings.__dict__, "request_timeout_s": timeout})
            return await fetcher.fetch(url)
        except Exception:  # noqa: BLE001
            return None

    try:
        return asyncio.run(asyncio.wait_for(_one(), timeout=timeout + 8))
    except Exception:  # noqa: BLE001
        return None


def sitemap_entries(fetcher, sitemap_url: str, depth: int = 0):
    """(url, lastmod) pairs from a sitemap, following one index level."""
    if depth > 2:
        return []
    r = _fetch(fetcher, sitemap_url, 20)
    if r is None or not r.ok:
        return []
    text = r.body.decode("utf-8", "replace")
    locs = LOC_RE.findall(text)
    if "<sitemapindex" in text:
        out = []
        for child in locs[:4]:
            out.extend(sitemap_entries(fetcher, child.strip(), depth + 1))
        return out
    mods = LASTMOD_RE.findall(text)
    pairs = []
    for i, loc in enumerate(locs):
        mod = mods[i] if i < len(mods) else ""
        pairs.append((loc.strip(), mod))
    return pairs


def pick_bilaspur(entries):
    """Newest Bilaspur-matching entries, newest first."""
    hits = [(u, m) for (u, m) in entries
            if any(t in u.lower() for t in BILASPUR_TERMS)]
    if not hits:
        return []
    def key(pair):
        return pair[1] or ""
    hits.sort(key=key, reverse=True)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "sitemap_probe.json"))
    parser.add_argument("--per-source", type=int, default=2)
    args = parser.parse_args()

    endpoints = json.loads(ENDPOINTS.read_text(encoding="utf-8"))
    targets = [e for e in endpoints if e.get("sitemap")]
    print(f"testing {len(targets)} sources with sitemaps\n", flush=True)

    settings = load_settings()
    results = []
    for entry in targets:
        fetcher = FetchManager(settings)
        sid = entry["source_id"]
        try:
            entries = sitemap_entries(fetcher, entry["sitemap"])
            hits = pick_bilaspur(entries)
            row = {
                "source_id": sid,
                "sitemap": entry["sitemap"],
                "sitemap_entries": len(entries),
                "bilaspur_hits": len(hits),
                "samples": [],
            }
            print(f"  {sid:44s} sm_entries={len(entries):<6d} bilaspur={len(hits)}",
                  flush=True)
            for url, mod in hits[:args.per_source]:
                art = _fetch(fetcher, url, 25)
                sample = {"url": url, "lastmod": mod,
                          "status": art.status_code if art else None,
                          "bytes": len(art.body) if art else 0,
                          "ok": False, "title": "", "body_chars": 0,
                          "parser": "", "date": None}
                if art is not None and art.ok and art.body:
                    out = extract_article(art, sid)
                    sample["ok"] = out.confidence_ok
                    sample["title"] = (out.fields.get("title") or "")[:90]
                    sample["body_chars"] = len(out.fields.get("body") or "")
                    sample["parser"] = out.parser_name
                    sample["date"] = str(out.fields.get("published") or "")
                    sample["reason"] = out.failure_reason
                row["samples"].append(sample)
                flag = "OK " if sample["ok"] else "FAIL"
                print(f"        {flag} http={sample['status']} "
                      f"body={sample['body_chars']:>5d} {sample['title'][:52]}",
                      flush=True)
            results.append(row)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sid:44s} ERROR {exc}", flush=True)
            results.append({"source_id": sid, "error": str(exc)})
        finally:
            import asyncio
            try:
                asyncio.run(fetcher.aclose())
            except Exception:  # noqa: BLE001
                pass

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    good = [r for r in results
            if any(s.get("ok") for s in r.get("samples", []))]
    print(f"\n{len(good)}/{len(results)} sources extracted a full article "
          f"over plain HTTP from their sitemap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
