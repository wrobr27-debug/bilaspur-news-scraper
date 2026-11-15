"""Discover the *sanctioned* discovery endpoints for each registry source.

For every source this looks for, in order:
  1. a sitemap advertised in robots.txt (`Sitemap:` directives)
  2. common sitemap paths (/sitemap.xml, /sitemap_index.xml, /sitemap.php,
     /news-sitemap.xml, /post-sitemap.xml)
  3. a JSON API the frontend calls (Next.js `/_next/data/...`, `/api/...`)

Read-only, one request per path, no retries. The output tells us which
sources can be collected with cheap HTTP and which genuinely need a
rendered browser.

Usage:
    python scripts/probe_endpoints.py
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit, urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.config.settings import load_settings  # noqa: E402
from bilaspur_scraper.fetching.fetcher import FetchManager  # noqa: E402

DB = ROOT / "data" / "news.db"

SITEMAP_PATHS = (
    "/sitemap.xml", "/sitemap_index.xml", "/sitemap.php", "/sitemap/",
    "/news-sitemap.xml", "/post-sitemap.xml", "/wp-sitemap.xml",
    "/sitemap-news.xml", "/sitemap1.xml",
)

#: JS bundle paths that reveal a site's JSON API. We do not call these
#: APIs blindly; we only look for the pattern so a human can confirm.
NEXT_DATA_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
API_HINT_RE = re.compile(
    r'["\'](/(?:api|_next/data)[^"\'\s]{3,90})["\']', re.IGNORECASE)


def _fetch(fetcher, url: str, timeout: int):
    import asyncio

    async def _one():
        try:
            fetcher._settings = type(fetcher._settings)(
                **{**fetcher._settings.__dict__, "request_timeout_s": timeout})
            res = await fetcher.fetch(url)
            return {
                "ok": res.ok, "status": res.status_code,
                "headers": {k.lower(): v for k, v in res.headers.items()},
                "body": res.body[:600_000],
                "final_url": res.final_url,
            }
        except Exception:  # noqa: BLE001
            return None

    try:
        return asyncio.run(asyncio.wait_for(_one(), timeout=timeout + 8))
    except Exception:  # noqa: BLE001
        return None


def probe(source: dict, fetcher: FetchManager) -> dict:
    base = source["url"]
    parts = urlsplit(base)
    origin = f"{parts.scheme}://{parts.netloc}"
    out = dict(source)
    out.update({"robots": None, "robots_sitemaps": [], "sitemap": None,
                "sitemap_entries": 0, "sitemap_kind": None,
                "next_build_id": None, "api_hints": [], "homepage_ok": False,
                "homepage_bytes": 0})

    # 1. robots.txt -> advertised sitemaps
    r = _fetch(fetcher, f"{origin}/robots.txt", 12)
    if r and r["ok"]:
        out["robots"] = r["status"]
        text = r["body"].decode("utf-8", "replace")
        out["robots_sitemaps"] = re.findall(
            r"(?im)^\s*sitemap\s*:\s*(\S+)", text)[:5]

    # 2. sitemap candidates
    candidates = list(out["robots_sitemaps"])
    candidates += [urljoin(origin, p) for p in SITEMAP_PATHS]
    seen = set()
    for cand in candidates:
        key = cand.split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        sr = _fetch(fetcher, cand, 12)
        if not sr or not sr["ok"]:
            continue
        body = sr["body"].decode("utf-8", "replace")
        if "<urlset" not in body and "<sitemapindex" not in body:
            continue
        kind = "index" if "<sitemapindex" in body else "urlset"
        n_urls = body.count("<loc>")
        out["sitemap"] = cand
        out["sitemap_kind"] = kind
        out["sitemap_entries"] = n_urls
        if n_urls > 0:
            break

    # 3. homepage -> next.js build id + api hints
    hr = _fetch(fetcher, base, 18)
    if hr and hr["ok"]:
        out["homepage_ok"] = True
        out["homepage_bytes"] = len(hr["body"])
        html = hr["body"].decode("utf-8", "replace")
        m = NEXT_DATA_RE.search(html)
        if m:
            out["next_build_id"] = m.group(1)
        hints = []
        for h in API_HINT_RE.findall(html):
            if h not in hints:
                hints.append(h)
        out["api_hints"] = hints[:8]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "endpoints.json"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT source_id, source_name, source_type, url FROM sources "
        "WHERE lifecycle_state != 'excluded' ORDER BY source_id")]
    conn.close()
    print(f"probing {len(rows)} sources for sanctioned endpoints\n", flush=True)

    settings = load_settings()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for row in rows:
            futures[pool.submit(probe, row, FetchManager(settings))] = row
        for fut in as_completed(futures, timeout=600):
            row = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = dict(row)
                res["error"] = f"{type(exc).__name__}: {exc}"
            results.append(res)
            print(f"  {res['source_id']:46s} sm={str(res.get('sitemap_kind')):7s}"
                  f" n={res.get('sitemap_entries', 0):<6d}"
                  f" next={str(res.get('next_build_id'))[:12]:12s}"
                  f" api={len(res.get('api_hints', []))}", flush=True)

    results.sort(key=lambda r: r["source_id"])
    Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
