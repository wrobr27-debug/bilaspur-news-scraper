"""Find videos published in a recent window (the default: the last 24 hours).

Discovery differs here from the initial dataset build. The dataset needed
*channels*; this needs *what those channels published recently*, so the
source of truth is each channel's **uploads playlist**, which is the
publisher's own ordered list of everything they have posted.

Three sources are used, in order of reliability:

  1. **YouTube Data API - uploads playlist** (`playlistItems.list`)
     The channel id `UC…` has an uploads playlist `UU…` (same id, U
     instead of C). One call returns the newest 50 uploads with exact
     timestamps. **1 quota unit per call**, so a full sweep of every
     account costs ~153 units against a 10,000/day allowance. A channel
     with more than 50 uploads in the window is continued with the
     `nextPageToken`.
     *This is the primary method.*

  2. **YouTube Data API - search** (`search.list`, `order=date`,
     `publishedAfter=`) - used for accounts where the uploads playlist
     cannot be resolved, and for finding Bilaspur videos on *national*
     channels that carry them as a small part of a broader feed.
     **100 quota units per call**, so it is used sparingly.

  3. **The official channel RSS feed** (`/feeds/videos.xml`) - keyless
     and sanctioned, but unreliable: it 404s for most channels unless
     the request carries a browser-like User-Agent, and it returns only
     the newest ~15 items. Used as a fallback, never as the primary.

Read-only. It never downloads media and never writes to the database.

Usage:
    python scripts/recent_videos.py --hours 24
    python scripts/recent_videos.py --hours 6 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.config.settings import load_env_file  # noqa: E402

load_env_file()

from bilaspur_scraper.processing.bilaspur_match import DISTRICT_LOCALITIES  # noqa: E402

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

#: A browser-like User-Agent. The official RSS feed 404s without one.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

#: Terms that mark a video as Bilaspur coverage.
BILASPUR_TERMS = (
    "bilaspur", "बिलासपुर", "bemetara", "behatar", "kota", "कोटा",
    "takhatpur", "तखतपुर", "ratanpur", "रतनपुर", "masturi", "मस्तूरी",
    "sarkanda", "सरकंडा", "koni", "कोनी", "pendra", "पेंड्रा", "pandaria",
    "पंडरिया", "bilha", "बिल्हा", "ambikapur", "अंबिकापुर", "gaurela",
    "गौरेला", "lormi", "लोरमी", "mungeli", "मुंगेली", "manendragarh",
    "sipat", "सिपत", "champa", "चांपा", "sakti", "सक्ती", "gadarwara",
    "bhela", "भेला", "nagar nigam bilaspur", "tirandaj", "जशपुर",
)


def _api(path: str, params: dict) -> dict | None:
    """Call the YouTube Data API; None on any failure."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return None
    query = urllib.parse.urlencode({**params, "key": key})
    req = urllib.request.Request(f"{YOUTUBE_API}/{path}?{query}",
                                 headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        print(f"    API error {exc.code} on {path}: {body}")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"    API request failed on {path}: {type(exc).__name__}")
        return None


def uploads_playlist(channel_id: str) -> str | None:
    """The `UU…` uploads playlist for a `UC…` channel id."""
    if not channel_id or not channel_id.startswith("UC"):
        return None
    return "UU" + channel_id[2:]


def fetch_uploads(channel_id: str, since: datetime, *, max_pages: int = 4) -> list[dict]:
    """Everything the channel uploaded since `since`, newest first.

    Stops as soon as a page contains nothing newer than the window, so
    an inactive channel costs one call.
    """
    playlist = uploads_playlist(channel_id)
    if not playlist:
        return []
    out: list[dict] = []
    token = None
    for _ in range(max_pages):
        params = {"part": "snippet,contentDetails", "playlistId": playlist,
                  "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = _api("playlistItems", params)
        if not data:
            break
        items = data.get("items") or []
        if not items:
            break
        reached_old = False
        for item in items:
            snippet = item.get("snippet") or {}
            published = snippet.get("publishedAt") or ""
            try:
                pub = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                continue
            if pub < since:
                reached_old = True
                continue
            out.append({
                "video_id": (item.get("contentDetails") or {}).get("videoId"),
                "title": snippet.get("title"),
                "published_at": published,
                "channel_id": channel_id,
                "method": "uploads_playlist",
            })
        if reached_old:
            break
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def fetch_search(channel_id: str, since: datetime, *, max_results: int = 50) -> list[dict]:
    """Videos on a channel published since `since`, via search.

    100 quota units per call - used only when the uploads playlist
    cannot be resolved.
    """
    data = _api("search", {
        "part": "snippet", "channelId": channel_id, "type": "video",
        "order": "date", "maxResults": max_results,
        "publishedAfter": since.astimezone(timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    if not data:
        return []
    out = []
    for item in data.get("items", []):
        snippet = item.get("snippet") or {}
        vid = (item.get("id") or {}).get("videoId")
        if not vid:
            continue
        out.append({
            "video_id": vid,
            "title": snippet.get("title"),
            "published_at": snippet.get("publishedAt"),
            "channel_id": channel_id,
            "method": "search",
        })
    return out


def fetch_rss(channel_id: str, since: datetime) -> list[dict]:
    """The official channel RSS feed - keyless fallback."""
    import re
    url = (f"https://www.youtube.com/feeds/videos.xml"
           f"?channel_id={channel_id}")
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    pubs = re.findall(r"<published>([^<]+)</published>", body)
    ids = re.findall(r"<yt:videoId>([^<]+)</yt:videoId>", body)
    titles = re.findall(r"<media:title>([^<]*)</media:title>", body)
    out = []
    for i, vid in enumerate(ids):
        published = pubs[i] if i < len(pubs) else ""
        try:
            pub = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if pub < since:
            continue
        out.append({
            "video_id": vid,
            "title": titles[i] if i < len(titles) else None,
            "published_at": published,
            "channel_id": channel_id,
            "method": "rss",
        })
    return out


def is_bilaspur(video: dict) -> list[str]:
    """Which Bilaspur terms appear in the title (or channel name)."""
    hay = f"{video.get('title') or ''} {video.get('channel_name') or ''}".lower()
    return [t for t in BILASPUR_TERMS if t.lower() in hay]


def load_accounts() -> list[dict]:
    """Every account in the dataset that has a channel id."""
    import sqlite3
    db = ROOT / "data" / "news.db"
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT account_id, platform, account_name, channel_id, profile_url, "
        "       verification_status, relevance "
        "FROM social_accounts WHERE channel_id IS NOT NULL "
        "ORDER BY account_name")]
    conn.close()
    return rows


def store(database_url: str, videos: list[dict], accounts: list[dict]) -> dict:
    """Write the newly discovered videos into the dataset."""
    from bilaspur_scraper.database.media_models import SocialVideo
    from bilaspur_scraper.database.session import (
        create_db_engine,
        create_schema,
        make_session_factory,
    )
    from sqlalchemy import select

    engine = create_db_engine(database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    by_channel = {a["channel_id"]: a for a in accounts}
    now = datetime.now(timezone.utc)
    added = 0
    updated = 0
    with factory() as session:
        for v in videos:
            account = by_channel.get(v["channel_id"])
            if account is None:
                continue
            existing = session.scalar(
                select(SocialVideo).where(
                    SocialVideo.platform == "youtube",
                    SocialVideo.video_id == v["video_id"]))
            if existing is not None:
                existing.last_seen_at = now
                existing.published_label = (v.get("published_at") or "")[:48]
                updated += 1
                continue
            pub = None
            raw = v.get("published_at")
            if raw:
                try:
                    pub = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    pub = None
            terms = v.get("bilaspur_terms") or []
            session.add(SocialVideo(
                account_id=account["account_id"],
                platform="youtube",
                video_id=v["video_id"],
                url=f"https://www.youtube.com/watch?v={v['video_id']}",
                title=(v.get("title") or "")[:2000] or None,
                published_at=pub,
                published_label=(raw or "")[:48],
                match_evidence=(f"published in the last {args_hours} h; "
                                f"matched {', '.join(terms)}"
                                if terms else
                                f"published in the last {args_hours} h"),
                match_terms=", ".join(terms) if terms else None,
                discovered_via=v.get("method") or "uploads_playlist",
                discovery_query=f"last {args_hours} hours",
                discovered_at=now, first_seen_at=now, last_seen_at=now))
            added += 1
        session.commit()
    engine.dispose()
    return {"added": added, "updated": updated}


#: set from main() so store() can quote the window in its evidence text
args_hours = 24


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--out", default=str(ROOT / "data" / "recent_videos.json"))
    parser.add_argument("--include-all", action="store_true",
                        help="keep videos with no Bilaspur term in the title")
    parser.add_argument("--dry-run", action="store_true",
                        help="do not write to the database")
    parser.add_argument("--database-url", default="sqlite:///data/news.db")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    global args_hours
    args_hours = args.hours
    print(f"looking for uploads since {since.isoformat()} "
          f"(last {args.hours} h)\n")

    accounts = load_accounts()
    print(f"{len(accounts)} accounts with a channel id\n")

    all_videos: list[dict] = []
    for account in accounts:
        cid = account["channel_id"]
        found = fetch_uploads(cid, since)
        method = "uploads_playlist"
        if not found:
            found = fetch_rss(cid, since)
            method = "rss"
        for v in found:
            v["channel_name"] = account["account_name"]
            v["account_id"] = account["account_id"]
            v["method"] = v.get("method") or method
            v["bilaspur_terms"] = is_bilaspur(v)
        all_videos.extend(found)
        mark = "★" if any(v["bilaspur_terms"] for v in found) else " "
        print(f"  {mark} {account['account_name'][:34]:36s} "
              f"{len(found):3d} uploads  ({method})", flush=True)

    all_videos.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    matched = [v for v in all_videos if v["bilaspur_terms"]]
    if not args.include_all:
        all_videos = matched

    Path(args.out).write_text(json.dumps({
        "since": since.isoformat(),
        "hours": args.hours,
        "channels_checked": len(accounts),
        "videos_found": len(all_videos),
        "videos": all_videos,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(all_videos)} videos from the last {args.hours} h "
          f"across {len(accounts)} channels")
    if not args.dry_run:
        counts = store(args.database_url, all_videos, accounts)
        print(f"stored: {counts['added']} new, {counts['updated']} already known")
    if matched:
        print(f"{len(matched)} of them name a Bilaspur place in the title")
        print("\nNEWEST:")
        for v in matched[:12]:
            when = (v.get("published_at") or "")[:16].replace("T", " ")
            print(f"  {when}  {v['channel_name'][:22]:24s} "
                  f"{(v.get('title') or '')[:48]}")
            print(f"      https://www.youtube.com/watch?v={v['video_id']}")
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
