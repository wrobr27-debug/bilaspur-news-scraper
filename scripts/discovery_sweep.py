"""Discovery sweep: run the real discovery engine over every registry
source and record which strategy found articles.

Read-only - it never writes to the database. Sources are probed
concurrently (different hosts only), and the result is saved after each
source so an interrupted sweep keeps what it found.

Usage:
    python scripts/discovery_sweep.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.config.settings import load_settings  # noqa: E402
from bilaspur_scraper.discovery.engine import DiscoveryEngine  # noqa: E402
from bilaspur_scraper.fetching.fetcher import FetchManager  # noqa: E402
from bilaspur_scraper.fetching.robots import RobotsGate  # noqa: E402

DB = ROOT / "data" / "news.db"


def registry_sources(states: tuple[str, ...]) -> list[dict]:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in states)
    rows = [dict(r) for r in conn.execute(
        f"SELECT source_id, source_name, source_type, url, coverage_scope "
        f"FROM sources WHERE lifecycle_state IN ({placeholders}) "
        f"ORDER BY source_id", states)]
    conn.close()
    return rows


def probe(source: dict, settings) -> dict:
    """Run the discovery engine for one source."""
    async def _one():
        fetcher = FetchManager(settings)
        try:
            robots = await RobotsGate(fetcher).check(source["url"])
            engine = DiscoveryEngine(fetcher,
                                     source_scope=source["coverage_scope"])
            engine.set_robots_sitemaps(robots.sitemaps or [])
            outcome = await engine.discover(source["url"], max_items=8,
                                            feed_only=False)
            return {
                "source_id": source["source_id"],
                "strategy": outcome.strategy_used,
                "items": len(outcome.items),
                "robots_ok": bool(robots.robots_ok) if hasattr(robots, "robots_ok")
                             else (not robots.unavailable and not robots.explicitly_disallowed),
                "sample": outcome.items[0].url if outcome.items else None,
                "titles": [i.title_hint for i in outcome.items[:3]
                           if i.title_hint],
                "errors": outcome.errors[:3],
            }
        except Exception as exc:  # noqa: BLE001
            return {"source_id": source["source_id"], "strategy": "ERROR",
                    "items": 0, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                await fetcher.aclose()
            except Exception:  # noqa: BLE001
                pass

    return asyncio.run(_one())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "discovery.json"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    states = ("candidate", "verified", "enabled", "disabled", "rejected")
    rows = registry_sources(states)
    settings = load_settings()
    print(f"discovery sweep over {len(rows)} sources "
          f"({args.workers} workers)\n", flush=True)

    results: list[dict] = []
    out_path = Path(args.out)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, row, settings): row for row in rows}
        for fut in as_completed(futures, timeout=1500):
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = {"source_id": futures[fut]["source_id"],
                       "strategy": "ERROR", "items": 0, "error": str(exc)}
            results.append(res)
            flag = {"rss": "FEED", "sitemap": "SITEMAP",
                    "listing": "LISTING"}.get(res["strategy"], "NONE")
            print(f"  {res['source_id']:44s} {flag:8s} n={res['items']:<2d} "
                  f"{str(res.get('sample') or '')[:56]}", flush=True)
            results.sort(key=lambda r: r["source_id"])
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    worked = [r for r in results if r["items"] > 0]
    print(f"\n{len(worked)}/{len(results)} sources discovered articles")
    by = {}
    for r in worked:
        by[r["strategy"]] = by.get(r["strategy"], 0) + 1
    print("by strategy:", by)
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
