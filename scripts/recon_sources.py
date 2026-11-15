"""Fast reconnaissance of candidate sources.

For each candidate this probes, in parallel and read-only:
  - robots.txt  (reachable? status?)
  - /feed/      (WordPress)
  - /rss.xml    (generic)
  - the homepage itself

It never writes to the database and never parses articles. Its only job
is to tell us which candidates have a machine-readable feed, so we know
which ones can be verified cheaply over HTTP and which genuinely need the
browser. Politeness: one request per candidate path, concurrent across
*different* hosts only, so no single host is hit twice at once.

Usage:
    python scripts/recon_sources.py            # probe the untried candidates
    python scripts/recon_sources.py --all      # probe every registry source
"""

from __future__ import annotations

import argparse
import json
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

FEED_PATHS = ("/feed/", "/rss.xml", "/feed", "/rss", "/atom.xml")


def candidates(only_untried: bool) -> list[dict]:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    sql = "SELECT source_id, source_name, source_type, url FROM sources"
    if only_untried:
        sql += " WHERE lifecycle_state IN ('candidate', 'disabled')"
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY source_id")]
    conn.close()
    return rows


def probe(source: dict, fetcher: FetchManager) -> dict:
    """Probe one source. One request per path, no retries, short timeout."""
    base = source["url"]
    result = dict(source)
    result["robots"] = None
    result["robots_status"] = None
    result["homepage"] = None
    result["homepage_status"] = None
    result["feed"] = None
    result["feed_status"] = None
    result["feed_entries"] = 0
    result["error"] = None

    parts = urlsplit(base)
    origin = f"{parts.scheme}://{parts.netloc}"

    # 1. robots.txt
    r = await_result(fetcher, f"{origin}/robots.txt", timeout=12)
    if r is not None:
        result["robots_status"] = r["status"]
        result["robots"] = r["ok"]

    # 2. homepage
    r = await_result(fetcher, base, timeout=15)
    if r is not None:
        result["homepage_status"] = r["status"]
        result["homepage"] = r["ok"]

    # 3. feed candidates - try EVERY path, keep the one with the most
    #    entries. A feed that parses but is empty must not stop the
    #    search (a publisher may expose an empty /rss.xml and a full
    #    /feed/).
    best_feed = None
    best_entries = 0
    tried = []
    for path in FEED_PATHS:
        r = await_result(fetcher, urljoin(origin, path), timeout=12)
        if r is None or not r["ok"]:
            tried.append({"path": path, "status": None, "entries": 0})
            continue
        ctype = (r["headers"].get("content-type") or "").lower()
        entries = count_entries(r["body"])
        tried.append({"path": path, "status": r["status"],
                      "entries": entries, "content_type": ctype})
        looks_xml = ("xml" in ctype or "rss" in ctype or "atom" in ctype
                     or entries > 0)
        if not looks_xml:
            continue
        if entries > best_entries:
            best_entries = entries
            best_feed = urljoin(origin, path)
    result["feed_paths_tried"] = tried
    if best_feed:
        result["feed"] = best_feed
        result["feed_status"] = 200
        result["feed_entries"] = best_entries
    return result


def await_result(fetcher, url: str, timeout: int):
    """One fetch, no retry, tolerant of failure.

    The fetch itself has a per-request timeout, but Crawlee's retry
    policy can stretch it. A hard wall-clock cap keeps one slow host
    from stalling the whole sweep.
    """
    import asyncio

    async def _one():
        try:
            fetcher._settings = type(fetcher._settings)(
                **{**fetcher._settings.__dict__, "request_timeout_s": timeout})
            res = await fetcher.fetch(url)
            return {
                "ok": res.ok,
                "status": res.status_code,
                "headers": {k.lower(): v for k, v in res.headers.items()},
                "body": res.body[:400_000],
                "error": res.error_code.value if res.error_code else None,
            }
        except Exception:  # noqa: BLE001
            return None

    try:
        return asyncio.run(asyncio.wait_for(_one(), timeout=timeout + 8))
    except Exception:  # noqa: BLE001
        return None


def count_entries(body: bytes) -> int:
    """Count <item>/<entry> elements without a full XML parse."""
    import re
    text = body.decode("utf-8", "replace")
    return len(re.findall(r"<item\b", text, re.IGNORECASE)) + \
        len(re.findall(r"<entry\b", text, re.IGNORECASE))


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe candidate sources")
    parser.add_argument("--all", action="store_true",
                        help="probe every registry source, not just untried")
    parser.add_argument("--out", default=str(ROOT / "data" / "recon.json"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    rows = candidates(only_untried=not args.all)
    print(f"probing {len(rows)} sources with {args.workers} workers\n")

    settings = load_settings()
    results: list[dict] = []
    # one fetcher per worker thread would be ideal; FetchManager is used
    # from a fresh event loop per request, so give each probe its own.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for row in rows:
            fetcher = FetchManager(settings)
            futures[pool.submit(probe, row, fetcher)] = row
        for fut in as_completed(futures, timeout=420):
            row = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = dict(row)
                res["error"] = f"{type(exc).__name__}: {exc}"
            results.append(res)
            flag = "FEED" if res.get("feed") else ("html" if res.get("homepage") else "----")
            print(f"  {res['source_id']:46s} robots={str(res.get('robots_status')):5s} "
                  f"home={str(res.get('homepage_status')):5s} {flag:5s} "
                  f"entries={res.get('feed_entries', 0)}", flush=True)

    results.sort(key=lambda r: r["source_id"])
    Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    with_feed = [r for r in results if r.get("feed")]
    print(f"\n{len(with_feed)}/{len(results)} have a usable feed")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
