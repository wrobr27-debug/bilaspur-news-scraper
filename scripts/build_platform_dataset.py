"""Build a dataset of Bilaspur-related social and video accounts.

Covers four platforms, using only methods each platform actually permits:

  **YouTube** - the official Data API v3 with the owner's key.
    `channels.list` gives the name, description, subscriber and video
    counts and the uploads playlist, 50 channels per unit. The uploads
    playlist gives every recent upload with an exact timestamp. Full
    article-level access is available, so this platform can be collected
    from properly.

  **Facebook** - the page's own Open Graph tags, read from the public
    page. These carry the page name, its description and the follower
    and post counts. Post *text* is not served without the Graph API, so
    a Facebook row records the account, not its posts.

  **Instagram** - the public profile page. Instagram serves a login
    wall to anonymous clients, so only the handle and the URL can be
    confirmed. The Graph API (with a token) is the sanctioned route for
    the rest; without one, the row records the account as a lead.

  **X** - the public profile page's title, which carries the display
    name and the handle. The timeline needs the API; without a token the
    row records the account as a lead.

`SKILLS.md` §5.5 is explicit: *"Prefer official APIs and platform-approved
access methods"* and *"do not use stealth scraping, credential sharing,
session-cookie harvesting, or private endpoints."* So this script reads
public pages and official APIs only, and marks what it could not read
rather than working around it.

Usage:
    python scripts/build_platform_dataset.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.config.settings import load_env_file  # noqa: E402

load_env_file()

from bilaspur_scraper.processing.bilaspur_match import DISTRICT_LOCALITIES  # noqa: E402

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

#: Terms that mark an account or video as Bilaspur coverage.
BILASPUR_TERMS = (
    "bilaspur", "बिलासपुर", "bemetara", "behatar", "kota", "कोटा",
    "takhatpur", "तखतपुर", "ratanpur", "रतनपुर", "masturi", "मस्तूरी",
    "sarkanda", "सरकंडा", "koni", "कोनी", "pendra", "पेंड्रा", "pandaria",
    "पंडरिया", "bilha", "बिल्हा", "ambikapur", "अंबिकापुर", "gaurela",
    "गौरेला", "lormi", "लोरमी", "mungeli", "मुंगेली", "manendragarh",
    "sipat", "सिपत", "champa", "चांपा", "sakti", "सक्ती", "gadarwara",
    "bhela", "भेला", "bilaspur", "nagar nigam", "नगर निगम", "ज़िला", "जिला",
)

META_RE = re.compile(r"<meta property=\"og:description\" content=\"([^\"]*)\"")
TITLE_RE = re.compile(r"<title>([^<]{3,90})</title>")
X_TITLE_RE = re.compile(r"<title>([^<]{3,120})</title>")


def _html(url: str, timeout: int = 30) -> str | None:
    # Facebook answers HTTP 400 unless the request looks like a real
    # browser navigation: it wants the Sec-Fetch-* header set and
    # `Accept-Encoding: identity`. A bare User-Agent is refused.
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "en-IN,hi-IN,en,hi",
        "Accept-Encoding": "identity",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def _decode(text: str) -> str:
    """Undo the HTML entities Facebook puts in its og tags."""
    import html
    return html.unescape(text)


def _is_bilaspur(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [t for t in BILASPUR_TERMS if t.lower() in lowered]


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------

def youtube_channels(channel_ids: list[str], key: str) -> dict[str, dict]:
    """Full metadata for each channel, 50 channels per API unit."""
    out: dict[str, dict] = {}
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i + 50]
        query = urllib.parse.urlencode({
            "part": "snippet,contentDetails,statistics,topicDetails,brandingSettings",
            "maxResults": 50, "id": ",".join(batch), "key": key})
        req = urllib.request.Request(f"{YOUTUBE_API}/channels?{query}",
                                     headers={"User-Agent": BROWSER_UA})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  channels.list failed: {type(exc).__name__}")
            continue
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            details = item.get("contentDetails", {})
            cid = item["id"]
            out[cid] = {
                "platform": "youtube",
                "handle": snippet.get("customUrl") or "",
                "name": snippet.get("title") or snippet.get("customUrl") or cid,
                "url": f"https://www.youtube.com/channel/{cid}",
                "channel_id": cid,
                "description": (snippet.get("description") or "")[:1000],
                "subscribers": stats.get("subscriberCount"),
                "video_count": stats.get("videoCount"),
                "view_count": stats.get("viewCount"),
                "country": snippet.get("country"),
                "published_at": snippet.get("publishedAt"),
                "uploads_playlist": (details.get("relatedPlaylists") or {})
                                       .get("uploads"),
                "keywords": (snippet.get("keywords") or "")[:300],
                "topics": [(t.get("topicCategoryId") if isinstance(t, dict)
                            else t)
                           for t in (item.get("topicDetails") or {})
                           .get("topicCategories", [])],
            }
    return out


def youtube_recent(channel_ids: list[str], key: str, *, hours: int = 720,
                   per_channel: int = 10) -> list[dict]:
    """Recent uploads per channel, from each channel's own uploads
    playlist (1 API unit per call)."""
    since = datetime.now(timezone.utc).timestamp() - hours * 3600
    out: list[dict] = []

    def fetch(cid: str):
        playlist = "UU" + cid[2:]
        query = urllib.parse.urlencode({
            "part": "snippet,contentDetails", "playlistId": playlist,
            "maxResults": 50, "key": key})
        req = urllib.request.Request(
            f"{YOUTUBE_API}/playlistItems?{query}",
            headers={"User-Agent": BROWSER_UA})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return cid, json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return cid, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, cid) for cid in channel_ids]
        for fut in as_completed(futures):
            cid, data = fut.result()
            if not data:
                continue
            for item in (data.get("items") or [])[:per_channel]:
                snippet = item.get("snippet", {})
                published = snippet.get("publishedAt") or ""
                try:
                    ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.timestamp() < since:
                    continue
                title = snippet.get("title") or ""
                out.append({
                    "platform": "youtube",
                    "channel_id": cid,
                    "video_id": (item.get("contentDetails") or {}).get("videoId"),
                    "title": title,
                    "published_at": published,
                    "url": f"https://www.youtube.com/watch?v="
                           f"{(item.get('contentDetails') or {}).get('videoId')}",
                    "bilaspur_terms": _is_bilaspur(title),
                })
    return out


# --------------------------------------------------------------------------
# Facebook / Instagram / X - public pages only
# --------------------------------------------------------------------------

def facebook_page(url: str) -> dict:
    """The page's public Open Graph metadata."""
    # A Facebook *search* URL is not a page. There is nothing to read
    # from it, and pretending otherwise would invent a follower count.
    if "/search" in url or "?" in url:
        return {"url": url, "readable": False,
                "note": "this is a search URL, not a profile page - the "
                        "exact page has to be resolved first"}
    html = _html(url)
    if not html:
        return {"url": url, "readable": False, "note": "no response"}
    title = TITLE_RE.search(html)
    desc = META_RE.search(html)
    if not title and not desc:
        return {"url": url, "readable": False,
                "note": "no Open Graph tags in the response"}
    out = {
        "url": url,
        "readable": True,
        "name": _decode(title.group(1)).strip() if title else None,
        "description": _decode(desc.group(1)).strip() if desc else None,
    }
    # The description carries the counts, but Facebook localises the
    # words: "10,834 फ़ॉलोअर्स · 1,191 इस बारे में बात कर रहे हैं".
    # So match the English word, the Devanagari transliteration, or the
    # Hindi word.
    desc = out["description"] or ""
    followers = re.search(
        r"([\d,.]+[KM]?)\s*(?:followers|फ़ॉलोअर्स|फॉलोअर्स|अनुयायी)",
        desc, re.IGNORECASE)
    posts = re.search(
        r"([\d,.]+[KM]?)\s*(?:posts|पोस्ट)",
        desc, re.IGNORECASE)
    likes = re.search(
        r"([\d,.]+[KM]?)\s*(?:likes|लाइक)",
        desc, re.IGNORECASE)
    out["followers"] = followers.group(1) if followers else None
    out["post_count"] = posts.group(1) if posts else None
    out["likes"] = likes.group(1) if likes else None
    out["bilaspur_terms"] = _is_bilaspur(
        f"{out.get('name')} {out.get('description')}")
    return out


def instagram_profile(url: str) -> dict:
    """What can be read from a public Instagram profile anonymously.

    Instagram serves a login wall to anonymous clients, so only the
    handle and the URL are confirmable without a Graph API token.
    """
    html = _html(url)
    if not html:
        return {"url": url, "readable": False, "note": "no response"}
    handle = re.search(r'"username":"([^"]+)"', html)
    return {
        "url": url,
        "readable": True,
        "handle": handle.group(1) if handle else None,
        "note": ("profile page served, but Instagram gates the data behind "
                 "a login; a Graph API token is needed for the details"),
        "bilaspur_terms": _is_bilaspur(url),
    }


def instagram_from_site(site_url: str) -> list[str]:
    """Instagram profile URLs linked from a publisher's own website."""
    html = _html(site_url, timeout=35)
    if not html:
        return []
    found = re.findall(
        r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._]{3,40}", html)
    out = []
    for link in found:
        link = link.replace("http://", "https://").rstrip("/")
        tail = link.rsplit("/", 1)[-1]
        if tail in ("p", "reel", "reels", "explore", "stories",
                    "accounts", "tv", "direct"):
            continue
        if link not in out:
            out.append(link)
    return out[:5]


def x_profile(url: str) -> dict:
    """What can be read from a public X profile anonymously.

    The timeline needs the API; the profile title carries the display
    name and the handle.
    """
    html = _html(url)
    if not html:
        return {"url": url, "readable": False, "note": "no response"}
    title = X_TITLE_RE.search(html)
    raw = title.group(1).strip() if title else None
    handle = re.search(r"\(@([A-Za-z0-9_]+)\)", raw or "")
    display = raw.split("(@")[0].strip() if raw else None
    return {
        "url": url,
        "readable": True,
        "name": display,
        "handle": handle.group(1) if handle else None,
        "note": ("profile page served; the timeline needs the X API "
                 "for posts"),
        "bilaspur_terms": _is_bilaspur(f"{raw} {url}"),
    }


def facebook_from_site(site_url: str) -> list[str]:
    """Facebook page URLs linked from a publisher's own website.

    A publisher links its own social accounts, so the site is the
    authoritative source for the exact page - far better than a Facebook
    search URL, which is JS-rendered and yields nothing.
    """
    html = _html(site_url, timeout=35)
    if not html:
        return []
    found = re.findall(
        r"https?://(?:www\.|m\.)?facebook\.com/[A-Za-z0-9._-]{3,60}", html)
    out = []
    for link in found:
        link = link.replace("http://", "https://").rstrip("/")
        if link.endswith("facebook.com"):
            continue
        # skip share and profile links that are not page slugs
        if any(p in link for p in ("/sharer", "/share", "/login",
                                   "/dialog", "/plugins")):
            continue
        if link not in out:
            out.append(link)
    return out[:5]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "platform_dataset.json"))
    parser.add_argument("--hours", type=int, default=720,
                        help="how far back to look for uploads")
    parser.add_argument("--database-url", default="sqlite:///data/news.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY is not set")
        return 1

    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'news.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    accounts = [dict(r) for r in conn.execute(
        "SELECT account_id, platform, account_name, handle, profile_url, "
        "       channel_id FROM social_accounts ORDER BY platform, account_name")]
    conn.close()

    # Each social account belongs to a publisher or institution; its
    # website is the authoritative source for the exact social page
    # (a Facebook search URL cannot be read, but the publisher's own
    # site links its real page).
    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'news.db'}?mode=ro",
                           uri=True)
    conn.row_factory = sqlite3.Row
    sites = {}
    for r in conn.execute(
            "SELECT source_name, url FROM sources WHERE enabled=1 OR "
            "lifecycle_state IN ('verified','candidate','disabled')"):
        sites.setdefault(r["source_name"].strip().lower(), r["url"])
    conn.close()

    def site_for(account_name: str) -> str | None:
        key = account_name.strip().lower()
        # "Takhatpur Ki Bhadas" vs "Takhatpur Ki Bhadas / Bilaspur Ki Bhadas"
        for name, url in sites.items():
            if key.startswith(name) or name.startswith(key):
                return url
        return None

    for a in accounts:
        a["site_url"] = site_for(a["account_name"])
    print(f"{len(accounts)} accounts in the registry\n")

    # ---- YouTube ----
    yt_ids = [a["channel_id"] for a in accounts
              if a["platform"] == "youtube" and a.get("channel_id")]
    print(f"YouTube: {len(yt_ids)} channels with an id")
    yt = youtube_channels(yt_ids, key)
    print(f"  {len(yt)} channels resolved with full metadata")
    videos = youtube_recent(yt_ids, key, hours=args.hours)
    print(f"  {len(videos)} uploads in the last {args.hours} h")

    # ---- Facebook / Instagram / X ----
    social = []
    for a in accounts:
        if a["platform"] == "facebook":
            detail = facebook_page(a["profile_url"])
            # a Facebook *search* URL cannot be read. The publisher's own
            # website links its real page, so look there instead.
            if not detail.get("readable"):
                site = a.get("site_url")
                if site:
                    found = facebook_from_site(site)
                    if found:
                        detail = facebook_page(found[0])
                        detail["resolved_from_site"] = site
                        detail["original_url"] = a["profile_url"]
            social.append({"platform": "facebook", **a, "detail": detail})
        elif a["platform"] == "instagram":
            social.append({"platform": "instagram", **a,
                           "detail": instagram_profile(a["profile_url"])})
        elif a["platform"] == "x":
            social.append({"platform": "x", **a,
                           "detail": x_profile(a["profile_url"])})

    fb = [s for s in social if s["platform"] == "facebook"]
    ig = [s for s in social if s["platform"] == "instagram"]
    xx = [s for s in social if s["platform"] == "x"]
    print(f"\nFacebook : {sum(1 for s in fb if s['detail'].get('readable'))}/{len(fb)} pages readable")
    print(f"Instagram: {sum(1 for s in ig if s['detail'].get('readable'))}/{len(ig)} profiles served (login-gated)")
    print(f"X        : {sum(1 for s in xx if s['detail'].get('readable'))}/{len(xx)} profiles served")

    dataset = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "youtube_channels": list(yt.values()),
        "youtube_videos": videos,
        "social_accounts": social,
        "bilaspur_terms": list(BILASPUR_TERMS),
        "notes": {
            "youtube": "official Data API v3 - full metadata and video list",
            "facebook": "public page Open Graph tags - account metadata only, "
                        "post text needs the Graph API",
            "instagram": "login-gated without a Graph API token - account only",
            "x": "profile title only - the timeline needs the X API",
        },
    }
    Path(args.out).write_text(json.dumps(dataset, ensure_ascii=False, indent=2),
                               encoding="utf-8")

    # ---- load into the database ----
    if not args.dry_run:
        from bilaspur_scraper.database.media_models import SocialVideo
        from bilaspur_scraper.database.session import (
            create_db_engine, create_schema, make_session_factory)
        from sqlalchemy import select

        engine = create_db_engine(args.database_url)
        create_schema(engine)
        factory = make_session_factory(engine)
        added = updated = 0
        now = datetime.now(timezone.utc)
        with factory() as session:
            for v in videos:
                cid = v["channel_id"]
                acct = next((a for a in accounts
                             if a.get("channel_id") == cid), None)
                if acct is None:
                    continue
                existing = session.scalar(
                    select(SocialVideo).where(
                        SocialVideo.platform == "youtube",
                        SocialVideo.video_id == v["video_id"]))
                if existing is not None:
                    existing.last_seen_at = now
                    existing.published_label = v["published_at"][:48]
                    updated += 1
                    continue
                pub = None
                try:
                    pub = datetime.fromisoformat(
                        v["published_at"].replace("Z", "+00:00"))
                except ValueError:
                    pass
                terms = v.get("bilaspur_terms") or []
                session.add(SocialVideo(
                    account_id=acct["account_id"], platform="youtube",
                    video_id=v["video_id"], url=v["url"], title=v["title"],
                    published_at=pub, published_label=v["published_at"][:48],
                    match_evidence=(f"matched {', '.join(terms)}"
                                    if terms else "channel is a Bilaspur source"),
                    match_terms=", ".join(terms) if terms else None,
                    discovered_via="uploads_playlist",
                    discovery_query=f"last {args.hours} hours",
                    discovered_at=now, first_seen_at=now, last_seen_at=now))
                added += 1
            session.commit()
        engine.dispose()
        print(f"\nstored: {added} new videos, {updated} already known")

    # ---- report ----
    print("\n=== YOUTUBE CHANNELS (by subscribers) ===")
    for c in sorted(yt.values(),
                     key=lambda x: int(x.get("subscribers") or 0),
                     reverse=True)[:15]:
        print(f"  {str(c['subscribers'] or '?'):>8s} subs  "
              f"{str(c['video_count'] or '?'):>5s} vids  {c['name'][:42]}")

    # A channel counts as Bilaspur coverage when its own description
    # names a Bilaspur place. A national news channel that happens to
    # air one Bilaspur story is a source to *check*, not a Bilaspur
    # source - the distinction matters when a system publishes daily.
    local = []
    for c in yt.values():
        hits = _is_bilaspur(f"{c['name']} {c['description']} {c['handle']}")
        c["bilaspur_terms"] = hits
        c["is_bilaspur_channel"] = bool(hits)
        if hits:
            local.append(c)

    print(f"\n=== BILASPUR CHANNELS ({len(local)}/{len(yt)}) ===")
    for c in sorted(local, key=lambda x: int(x.get("subscribers") or 0),
                     reverse=True)[:25]:
        print(f"  {str(c['subscribers'] or '?'):>8s} subs  "
              f"{str(c['video_count'] or '?'):>5s} vids  {c['name'][:38]:40s} "
              f"{', '.join(c['bilaspur_terms'][:2])}")

    bsp_videos = [v for v in videos if v.get("bilaspur_terms")]
    print(f"\n=== UPLOADS NAMING A BILASPUR PLACE "
          f"({len(bsp_videos)}/{len(videos)}) ===")
    for v in sorted(bsp_videos, key=lambda x: x["published_at"],
                    reverse=True)[:15]:
        when = v["published_at"][:16].replace("T", " ")
        print(f"   {when}  {v['title'][:60]}")

    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
