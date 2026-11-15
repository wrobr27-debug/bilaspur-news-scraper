"""Discover Bilaspur-related YouTube channels by searching YouTube itself.

A YouTube search results page is server-rendered: it carries a list of
`videoRenderer` objects, each with the video's id, title, channel id and
channel name. Those are the videos YouTube itself ranks for a Bilaspur
query, and each one's channel is a candidate publisher.

That gives a channel list derived from actual Bilaspur coverage rather
than from a directory someone typed by hand.

Read-only. No API key, no login, no media download.

Usage:
    python scripts/youtube_search.py --query "bilaspur news"
    python scripts/youtube_search.py --all-queries
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.processing.bilaspur_match import DISTRICT_LOCALITIES  # noqa: E402

USER_AGENT = ("Mozilla/5.0 (compatible; BilaspurNewsBot/0.1; "
              "+mailto:wrobr36@gmail.com)")

#: Queries that surface Bilaspur coverage. Each is a plausible way a
#: viewer would look for it.
DEFAULT_QUERIES = [
    "bilaspur news",
    "bilaspur cg news",
    "bilaspur chhattisgarh news",
    "bilaspur vlog",
    "bilaspur journalist",
    "bilaspur crime news",
    "bilaspur development news",
    "bilaspur politics news",
    "bilaspur railway news",
    "bilaspur weather news",
    "तखतपुर न्यूज़",
    "रतनपुर न्यूज़",
    "कोटा बिलासपुर न्यूज़",
    "बिलासपुर पुलिस",
    "बिलासपुर नगर निगम",
    "bilaspur municipal corporation news",
    "bilaspur collectorate news",
    "bilaspur railway division news",
]

#: Terms that mark a video or channel as Bilaspur coverage.
BILASPUR_TERMS = (
    "bilaspur", "बिलासपुर", "bemetara", "behatar", "kota", "कोटा",
    "takhatpur", "तखतपुर", "ratanpur", "रतनपुर", "masturi", "मस्तूरी",
    "sarkanda", "सरकंडा", "koni", "कोनी", "pendra", "पेंड्रा", "pandaria",
    "पंडरिया", "bilha", "बिल्हा", "ambikapur", "अंबिकापुर", "gaurela",
    "गौरेला", "lormi", "लोरमी", "mungeli", "मुंगेली", "manendragarh",
    "sipat", "सिपत", "champa", "चांपा", "sakti", "सक्ती", "gadarwara",
    "गडरवारा", "bhela", "भेला", "nagar nigam bilaspur", "tirandaj",
)

VIDEO_ID_RE = re.compile(r'"videoId":"([\w-]{11})"')
# The title sits *before* ownerText inside a videoRenderer, and is
# rendered as {"runs":[{"text":"..."}]}. A generous window plus a
# field-order check keeps it from grabbing a neighbour's title.
VIDEO_TITLE_RE = re.compile(
    r'"title":\{"runs":\[\{"text":"([^"]+)"')
CHANNEL_ID_RE = re.compile(r'"ownerText":\{.{0,800}?"(?:browseId|channelId)":"(UC[\w-]{22})"')
CHANNEL_NAME_RE = re.compile(
    r'"ownerText":\{.{0,800}?"runs":\[\{"text":"([^"]+)"')
VIEW_COUNT_RE = re.compile(r'"viewCountText":\{[^}]*?"simpleText":"([^"]+)"')
PUBLISHED_RE = re.compile(r'"publishedTimeText":\{[^}]*?"simpleText":"([^"]+)"')


def _get(url: str, timeout: int = 30) -> str | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-IN, hi-IN, en;q=0.8, hi;q=0.7",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def search(query: str, limit: int = 30) -> list[dict]:
    """Videos YouTube ranks for a query, with their channel."""
    url = ("https://www.youtube.com/results?search_query="
           + urllib.parse.quote_plus(query))
    html = _get(url)
    if not html:
        return []
    # split on the renderer boundaries so each chunk is one video
    chunks = html.split('"videoRenderer"')[1:]
    out = []
    for chunk in chunks[:limit * 2]:
        vid = VIDEO_ID_RE.search(chunk)
        if not vid:
            continue
        # the fields must all belong to this renderer
        owner_at = chunk.find('"ownerText"')
        title_at = chunk.find('"title"')
        if owner_at == -1 or title_at == -1 or title_at > owner_at:
            # title after ownerText belongs to a different renderer
            continue
        title = VIDEO_TITLE_RE.search(chunk[:owner_at])
        cid = CHANNEL_ID_RE.search(chunk)
        cname = CHANNEL_NAME_RE.search(chunk)
        views = VIEW_COUNT_RE.search(chunk)
        published = PUBLISHED_RE.search(chunk)
        out.append({
            "query": query,
            "video_id": vid.group(1),
            "title": title.group(1) if title else None,
            "channel_id": cid.group(1) if cid else None,
            "channel_name": cname.group(1) if cname else None,
            "views": views.group(1) if views else None,
            "published": published.group(1) if published else None,
            "url": f"https://www.youtube.com/watch?v={vid.group(1)}",
        })
        if len(out) >= limit:
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", action="append", default=[],
                        help="a search query (repeatable)")
    parser.add_argument("--all-queries", action="store_true",
                        help="use the built-in Bilaspur query list")
    parser.add_argument("--out", default=str(ROOT / "data" / "youtube_search.json"))
    args = parser.parse_args()

    queries = list(args.query)
    if args.all_queries or not queries:
        queries += DEFAULT_QUERIES
    queries = list(dict.fromkeys(queries))

    print(f"searching YouTube for {len(queries)} queries\n", flush=True)
    all_videos: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(search, q): q for q in queries}
        for fut in as_completed(futures, timeout=420):
            q = futures[fut]
            try:
                res = fut.result()
            except Exception:  # noqa: BLE001
                res = []
            all_videos.extend(res)
            print(f"  {q:44s} {len(res):3d} videos", flush=True)

    # a channel is a candidate if its videos look like Bilaspur coverage
    channels: dict[str, dict] = {}
    for v in all_videos:
        cid = v.get("channel_id")
        if not cid:
            continue
        entry = channels.setdefault(cid, {
            "channel_id": cid,
            "channel_name": v.get("channel_name"),
            "channel_url": f"https://www.youtube.com/channel/{cid}",
            "queries": set(),
            "videos": [],
            "bilaspur_hits": 0,
        })
        entry["queries"].add(v["query"])
        hay = f"{v.get('title') or ''} {v.get('channel_name') or ''}".lower()
        hits = [t for t in BILASPUR_TERMS if t.lower() in hay]
        if hits:
            entry["bilaspur_hits"] += 1
            v["bilaspur_terms"] = hits
        entry["videos"].append(v)

    out_channels = []
    for entry in channels.values():
        entry["queries"] = sorted(entry["queries"])
        out_channels.append(entry)
    out_channels.sort(key=lambda e: -e["bilaspur_hits"])

    Path(args.out).write_text(json.dumps({
        "queries": queries,
        "channels": out_channels,
        "videos": all_videos,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    strong = [c for c in out_channels if c["bilaspur_hits"] >= 2]
    print(f"\n{len(all_videos)} videos, {len(out_channels)} distinct channels, "
          f"{len(strong)} with >=2 Bilaspur hits")
    print("\nTOP CHANNELS:")
    for c in out_channels[:20]:
        print(f"  {c['bilaspur_hits']:3d} hits  {str(c['channel_name'])[:38]:40s} "
              f"{c['channel_id']}")
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
