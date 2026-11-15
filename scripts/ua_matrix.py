"""Compare User-Agent strings against the sites that refuse us.

Some publishers' WAFs reject a bot-shaped User-Agent outright but serve
a browser-compatible one. This probes each target with a set of User-Agent
strings and reports the status code for each, so we can tell apart:

  - "the site is down"          (every UA fails)
  - "the site blocks our UA"    (a browser-compatible UA succeeds)
  - "the site blocks everyone"  (every UA gets 403)

Read-only: one request per (target, UA) pair.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

USER_AGENTS = {
    # our current, fully honest identity
    "honest_bot": "BilaspurNewsBot/0.1 (+mailto:wrobr36@gmail.com)",
    # honest identity, but browser-compatible token shape
    "compatible": ("Mozilla/5.0 (compatible; BilaspurNewsBot/0.1; "
                   "+mailto:wrobr36@gmail.com)"),
    # what News-Agent uses
    "news_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"),
    # a plain browser
    "browser": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"),
}

TARGETS = [
    ("secl-cil", "https://secl-cil.in/robots.txt"),
    ("cgpolice", "https://bilaspur.cgpolice.gov.in/robots.txt"),
    ("cgpolice-home", "https://bilaspur.cgpolice.gov.in/"),
    ("ndtv-mpcg", "https://mpcg.ndtv.in/robots.txt"),
    ("ndtv-mpcg-home", "https://mpcg.ndtv.in/chhattisgarh-news/bilaspur"),
    ("bilaspur-gov", "https://bilaspur.gov.in/robots.txt"),
    ("bhaskar", "https://www.bhaskar.com/chhattisgarh/bilaspur/"),
    ("patrika", "https://www.patrika.com/bilaspur-news/"),
    ("naidunia", "https://www.naidunia.com/chhattisgarh/bilaspur"),
    ("amar-ujala", "https://www.amarujala.com/video/chhattisgarh/bilaspur-chhattisgarh"),
    ("livehindustan", "https://www.livehindustan.com/chhattisgarh/bilaspur/news"),
    ("abvv", "https://abvv.ac.in/robots.txt"),
    ("ggu", "https://www.ggu.ac.in/robots.txt"),
    ("nigam", "https://nigambilaspur.com/robots.txt"),
    ("smartcity", "https://www.icccbilaspur.in/robots.txt"),
    ("cmo", "https://cmo.cg.gov.in/robots.txt"),
    ("secr-rail", "https://secr.indianrailways.gov.in/"),
]


def probe(name: str, url: str, ua_key: str, ua: str) -> dict:
    cmd = [
        "curl", "-sSL", "-o", "/dev/null", "--max-time", "30",
        "-w", "%{http_code} %{size_download} %{time_total}",
        "-A", ua, url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=40).stdout.strip().split()
    except Exception:  # noqa: BLE001
        out = []
    status = int(out[0]) if out and out[0].isdigit() else 0
    size = int(out[1]) if len(out) > 1 and out[1].isdigit() else 0
    secs = float(out[2]) if len(out) > 2 else 0.0
    return {"target": name, "url": url, "ua": ua_key,
            "status": status, "bytes": size, "seconds": secs}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "ua_matrix.json"))
    args = parser.parse_args()

    jobs = [(n, u, k, ua) for (n, u) in TARGETS for k, ua in USER_AGENTS.items()]
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(probe, *j) for j in jobs]
        for fut in as_completed(futures, timeout=300):
            results.append(fut.result())

    results.sort(key=lambda r: (r["target"], r["ua"]))
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")

    by_target: dict[str, dict[str, dict]] = {}
    for r in results:
        by_target.setdefault(r["target"], {})[r["ua"]] = r

    print(f"{'TARGET':<16} {'honest_bot':>11} {'compatible':>11} "
          f"{'news_agent':>11} {'browser':>9}   VERDICT")
    for target in [t[0] for t in TARGETS]:
        row = by_target.get(target, {})
        cells = []
        for k in ("honest_bot", "compatible", "news_agent", "browser"):
            st = row.get(k, {}).get("status", 0)
            cells.append(st)
        statuses = [c for c in cells if c]
        if not statuses:
            verdict = "DOWN for everyone"
        elif all(c == 403 for c in statuses):
            verdict = "BLOCKED for everyone"
        elif cells[0] in (403, 0) and any(c == 200 for c in cells[1:]):
            verdict = "-> blocks OUR UA only"
        elif all(c == 200 for c in statuses):
            verdict = "OK for everyone"
        else:
            verdict = "mixed"
        print(f"{target:<16} {cells[0]:>11} {cells[1]:>11} {cells[2]:>11} "
              f"{cells[3]:>9}   {verdict}")

    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
