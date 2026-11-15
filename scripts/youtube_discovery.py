"""Discover Bilaspur-related YouTube channels and their videos.

**No API key required.** YouTube publishes an official RSS feed for every
channel (`/feeds/videos.xml?channel_id=...`), and a channel's `channelId`
can be read from its public page. Both are first-party, keyless and
sanctioned.

What this does:
  1. resolves a channel handle / URL to its channel id and metadata
  2. reads the channel's official RSS feed for its latest videos
  3. keeps only videos whose title, description or tags mention Bilaspur
     or a district locality
  4. writes a JSON dataset of channels and matching videos

Read-only. It never downloads media and never writes to the database.

Usage:
    python scripts/youtube_discovery.py --handles tirandaj,takhatpurkibhadas
    python scripts/youtube_discovery.py --from-workbook
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.processing.bilaspur_match import DISTRICT_LOCALITIES  # noqa: E402

USER_AGENT = ("Mozilla/5.0 (compatible; BilaspurNewsBot/0.1; "
              "+mailto:wrobr36@gmail.com)")

#: Terms that mark a video as Bilaspur coverage.
BILASPUR_TERMS = (
    "bilaspur", "बिलासपुर", "bemetara", "behatar", "kota", "कोटा",
    "takhatpur", "तखतपुर", "ratanpur", "रतनपुर", "masturi", "मस्तूरी",
    "sarkanda", "सरकंडा", "koni", "कोनी", "pendra", "पेंड्रा", "pandaria",
    "पंडरिया", "bilha", "बिल्हा", "ambikapur", "अंबिकापुर", "gaurela",
    "गौरेला", "lormi", "लोरमी", "mungeli", "मुंगेली", "manendragarh",
    "मनेंद्रगढ़", "sipat", "सिपत", "mungh", "चांपा", "sakti", "सक्ती",
    "kormu", "कोंरू", "gadarwara", "गडरवारा",
)

CHANNEL_ID_RE = re.compile(r'"(?:channelId|externalId)"\s*:\s*"(UC[\w-]{22})"')
SUB_RE = re.compile(r'"subscriberCountText".*?"simpleText"\s*:\s*"([^"]+)"')
VIDEO_COUNT_RE = re.compile(
    r'"videoCountText".*?"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"')
CHANNEL_TITLE_RE = re.compile(
    r'<meta property="og:title" content="([^"]+)"')
RSS_TITLE_RE = re.compile(r"<media:title>([^<]*)</media:title>")
RSS_LINK_RE = re.compile(r"<media:content[^>]*url=\"([^\"]+)\"")
RSS_ID_RE = re.compile(r"<yt:videoId>([^<]+)</yt:videoId>")
RSS_PUB_RE = re.compile(r"<published>([^<]+)</published>")


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


def normalise_channel(input_ref: str) -> str | None:
    """Turn a handle, channel URL or channel id into a channel id."""
    ref = input_ref.strip()
    if re.fullmatch(r"UC[\w-]{22}", ref):
        return ref
    if "/channel/" in ref:
        m = re.search(r"/channel/(UC[\w-]{22})", ref)
        if m:
            return m.group(1)
    if "youtube.com" in ref or "youtu.be" in ref:
        html = _get(ref)
        if not html:
            return None
        m = CHANNEL_ID_RE.search(html)
        return m.group(1) if m else None
    # a bare handle
    html = _get(f"https://www.youtube.com/@{ref.lstrip('@')}")
    if not html:
        return None
    m = CHANNEL_ID_RE.search(html)
    return m.group(1) if m else None


def channel_meta(channel_id: str, handle: str | None = None) -> dict:
    """Public metadata for a channel (name, subscribers, video count)."""
    url = (f"https://www.youtube.com/channel/{channel_id}" if channel_id
           else f"https://www.youtube.com/@{handle}")
    html = _get(url) or ""
    out = {
        "channel_id": channel_id,
        "handle": handle,
        "url": url,
        "name": None,
        "subscribers": None,
        "videos": None,
    }
    m = CHANNEL_TITLE_RE.search(html)
    if m:
        out["name"] = m.group(1)
    m = SUB_RE.search(html)
    if m:
        out["subscribers"] = m.group(1)
    m = VIDEO_COUNT_RE.search(html)
    if m:
        out["videos"] = m.group(1)
    return out


def channel_videos(channel_id: str, limit: int = 15) -> list[dict]:
    """The channel's latest videos from its official RSS feed."""
    xml = _get(f"https://www.youtube.com/feeds/videos.xml"
               f"?channel_id={channel_id}") or ""
    ids = RSS_ID_RE.findall(xml)
    titles = RSS_TITLE_RE.findall(xml)
    pubs = RSS_PUB_RE.findall(xml)
    links = RSS_LINK_RE.findall(xml)
    out = []
    for i, vid in enumerate(ids[:limit]):
        out.append({
            "video_id": vid,
            "title": titles[i] if i < len(titles) else None,
            "published": pubs[i] if i < len(pubs) else None,
            "url": (f"https://www.youtube.com/watch?v={vid}"),
        })
    return out


def is_bilaspur(video: dict) -> tuple[bool, list[str]]:
    """Does this video look like Bilaspur coverage?"""
    haystack = f"{video.get('title') or ''} {' '.join(video.get('terms', []))}".lower()
    hits = [t for t in BILASPUR_TERMS if t.lower() in haystack]
    return bool(hits), hits


def probe_channel(ref: str, limit: int = 15) -> dict:
    """Resolve one channel reference and list its Bilaspur videos."""
    channel_id = normalise_channel(ref)
    if not channel_id:
        return {"reference": ref, "resolved": False,
                "error": "could not resolve a channel id"}
    meta = channel_meta(channel_id, handle=ref if not ref.startswith("http") else None)
    videos = channel_videos(channel_id, limit=limit)
    matching = []
    for v in videos:
        # the feed title alone is the signal available without the API
        hits = [t for t in BILASPUR_TERMS if t.lower() in (v.get("title") or "").lower()]
        if hits:
            v["bilaspur_terms"] = hits
            matching.append(v)
    meta.update({"reference": ref, "resolved": True,
                 "rss_video_count": len(videos),
                 "bilaspur_videos": matching,
                 "bilaspur_video_count": len(matching)})
    return meta


def handles_from_workbook() -> list[str]:
    """YouTube references the workbook already lists."""
    import openpyxl
    path = Path.home() / "Downloads" / \
        "bilaspur_media_public_figures_directory_updated.xlsx"
    if not path.exists():
        return []
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["Platform Accounts"]
    out = []
    for row in list(ws.iter_rows(values_only=True))[1:]:
        if str(row[1]).strip().lower() != "youtube":
            continue
        url = str(row[3] or "").strip()
        if not url:
            continue
        # keep only real channel references, not search-result URLs
        if "/results?search_query=" in url or "/@" not in url and "/channel/" not in url:
            continue
        out.append(url)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handles", default="",
                        help="comma-separated handles, channel URLs or ids")
    parser.add_argument("--from-workbook", action="store_true",
                        help="use the YouTube references in the workbook")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--out", default=str(ROOT / "data" / "youtube_channels.json"))
    args = parser.parse_args()

    refs: list[str] = []
    if args.handles:
        refs += [h.strip() for h in args.handles.split(",") if h.strip()]
    if args.from_workbook:
        refs += handles_from_workbook()
    if not refs:
        print("nothing to probe: pass --handles or --from-workbook")
        return 1

    print(f"probing {len(refs)} channel reference(s)\n", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(probe_channel, r, args.limit): r for r in refs}
        for fut in as_completed(futures, timeout=420):
            ref = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = {"reference": ref, "resolved": False, "error": str(exc)}
            results.append(res)
            if res.get("resolved"):
                print(f"  {res.get('name') or res['reference'][:38]:40s} "
                      f"{res.get('channel_id')} "
                      f"subs={str(res.get('subscribers')):8s} "
                      f"bsp_vids={res.get('bilaspur_video_count')}", flush=True)
            else:
                print(f"  {ref[:44]:46s} UNRESOLVED: "
                      f"{str(res.get('error'))[:40]}", flush=True)

    results.sort(key=lambda r: r["reference"])
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    resolved = [r for r in results if r.get("resolved")]
    with_vids = [r for r in resolved if r.get("bilaspur_video_count")]
    print(f"\n{len(resolved)}/{len(results)} channels resolved, "
          f"{len(with_vids)} have Bilaspur videos in their latest feed")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
