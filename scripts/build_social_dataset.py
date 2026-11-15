"""Build the social / video source dataset and load it into SQLite.

Combines three inputs into one dataset:

  1. the workbook's **Platform Accounts** sheet (46 rows) - the owner's
     own list, with ownership evidence and a collection-method hint;
  2. **YouTube search discovery** (`data/youtube_search.json`) - channels
     YouTube itself ranks for Bilaspur queries;
  3. **YouTube channel resolution** (`data/youtube_channels.json`) - real
     channel ids for the handles the workbook lists.

A row that comes from a search-result URL (not a direct profile URL) is
recorded as `search_link_only` - a lead, not a confirmed account.

Usage:
    python scripts/build_social_dataset.py
    python scripts/build_social_dataset.py --out data/social_accounts.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.database.media_models import (  # noqa: E402
    MediaTranscript,
    SocialAccount,
    SocialVideo,
)
from bilaspur_scraper.database.session import (  # noqa: E402
    create_db_engine,
    create_schema,
    make_session_factory,
)
from sqlalchemy import select  # noqa: E402

DEFAULT_WORKBOOK = (Path.home() / "Downloads" /
                    "bilaspur_media_public_figures_directory_updated.xlsx")

#: A direct profile URL, as opposed to a search-results URL.
DIRECT_PATTERNS = {
    "youtube": r"youtube\.com/(?:@|channel/|c/|user/)",
    "instagram": r"instagram\.com/(?!explore|search|p/)",
    "facebook": r"facebook\.com/(?!search|pages/)",
    "x": r"(?:twitter|x)\.com/(?!search|home|intent)",
}

PLATFORM_ALIASES = {
    "youtube": "youtube", "instagram": "instagram",
    "facebook": "facebook", "x": "x", "twitter": "x",
}


#: The canonical verification vocabulary (mirrors the website registry).
#: The workbook's free-text "Verification status" column is mapped onto
#: it, so the two registries can be filtered together.
VERIFICATION_STATES = (
    "verified_official", "publisher_linked", "account_identified",
    "search_link_only", "unverified", "excluded",
)


def normalise_verification(text: str | None, *, is_direct: bool) -> str:
    """Map the workbook's free-text status onto the canonical vocabulary.

    The workbook writes sentences ("Publisher-linked; activity pending"),
    not enum values. The *leading* clause carries the status; the rest
    is a note. A row whose URL is only a search URL can never be better
    than `search_link_only`, whatever the note claims.
    """
    raw = (text or "").strip().lower()
    if not raw:
        return "unverified"
    head = raw.split(";")[0].split(",")[0].strip()

    if "excluded" in head:
        state = "excluded"
    elif "search" in head or "discovery url" in head or "link hub" in head:
        state = "search_link_only"
    elif "official" in head and ("site" in head or "linked" in head):
        state = "verified_official"
    elif "official" in head:
        state = "verified_official"
    elif "publisher" in head:
        state = "publisher_linked"
    elif "institution" in head or "company-listed" in head or "cmo" in head:
        state = "verified_official"
    elif "identified" in head or "exact channel pending" in head:
        state = "account_identified"
    elif "unverified" in head or "pending" in head:
        state = "unverified"
    else:
        state = "account_identified"

    # a search URL cannot be a confirmed account
    if not is_direct and state in ("verified_official", "publisher_linked",
                                   "account_identified"):
        return "search_link_only"
    return state


def account_id_for(platform: str, name: str, url: str) -> str:
    """A stable primary key: platform + a slug of the name.

    The URL is folded in so two different accounts that happen to share a
    name (two publishers called "News") do not collide.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    tail = re.sub(r"[^a-z0-9]+", "-", str(url).strip().lower()).strip("-")
    # keep the distinguishing tail of the URL
    tail = tail[-40:].strip("-")
    aid = f"{platform}-{slug}" if not tail else f"{platform}-{slug}-{tail}"
    return aid[:160]


def is_direct_profile(platform: str, url: str) -> bool:
    pattern = DIRECT_PATTERNS.get(platform)
    if not pattern:
        return False
    return bool(re.search(pattern, url, re.IGNORECASE))


def read_workbook(path: Path) -> list[dict]:
    """The Platform Accounts sheet, as structured rows."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["Platform Accounts"]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h or "").strip() for h in rows[0]]
    out = []
    for i, row in enumerate(rows[1:], start=2):
        rec = dict(zip(header, row))
        platform = PLATFORM_ALIASES.get(
            str(rec.get("Platform") or "").strip().lower(),
            str(rec.get("Platform") or "").strip().lower())
        if not platform:
            continue
        url = str(rec.get("Profile / discovery URL") or "").strip()
        name = str(rec.get("Account / entity") or "").strip()
        out.append({
            "origin_sheet": "Platform Accounts",
            "origin_row": i,
            "platform": platform,
            "account_name": name,
            "handle": str(rec.get("Handle / account name") or "").strip() or None,
            "profile_url": url,
            "account_type": str(rec.get("Account type") or "").strip() or "unknown",
            "ownership_evidence": str(rec.get("Ownership evidence") or "").strip() or None,
            "verification_status": str(rec.get("Verification status") or "").strip() or "unverified",
            "relevance": str(rec.get("Bilaspur relevance") or "").strip() or None,
            "collectable_via": str(rec.get("Collection method candidate") or "").strip() or None,
            "notes": str(rec.get("Notes") or "").strip() or None,
        })
    return out


def merge_search_results(accounts: list[dict], search: dict) -> list[dict]:
    """Add the channels YouTube search surfaced."""
    known = {a["profile_url"] for a in accounts}
    for ch in search.get("channels", []):
        cid = ch.get("channel_id")
        if not cid:
            continue
        url = ch.get("channel_url") or f"https://www.youtube.com/channel/{cid}"
        if url in known:
            continue
        known.add(url)
        hits = ch.get("bilaspur_hits", 0)
        accounts.append({
            "origin_sheet": "YouTube search",
            "origin_row": None,
            "platform": "youtube",
            "account_name": ch.get("channel_name") or cid,
            "handle": None,
            "profile_url": url,
            "channel_id": cid,
            "account_type": "news channel (search-discovered)",
            "ownership_evidence": (
                f"{hits} Bilaspur-matching videos in YouTube search results"),
            "verification_status": "account_identified",
            "relevance": "High" if hits >= 2 else "Medium",
            "collectable_via": "YouTube RSS feed (no key required)",
            "notes": (f"surfaced by {len(ch.get('queries', []))} Bilaspur "
                      f"queries; {hits} matching videos"),
            "discovery_method": "youtube_search",
            "discovery_evidence": ", ".join(ch.get("queries", [])[:6]),
        })
    return accounts


def merge_channel_resolution(accounts: list[dict], resolved: list[dict]) -> list[dict]:
    """Attach the real channel id to a handle the workbook lists."""
    by_ref = {}
    for r in resolved:
        if r.get("resolved") and r.get("channel_id"):
            by_ref[r["reference"].strip().lower()] = r
    for a in accounts:
        if a["platform"] != "youtube" or a.get("channel_id"):
            continue
        url = a["profile_url"]
        for ref in (url, url.rstrip("/"), url.replace("https://", ""),
                    url.replace("https://www.youtube.com/", "")):
            hit = by_ref.get(str(ref).lower())
            if hit:
                a["channel_id"] = hit["channel_id"]
                a["resolved_name"] = hit.get("name")
                a["subscribers"] = hit.get("subscribers")
                a["video_count"] = hit.get("videos")
                a["discovery_method"] = "workbook+resolved"
                break
    return accounts


def load_dataset(accounts: list[dict], search: dict, database_url: str) -> dict:
    """Write the dataset into SQLite."""
    engine = create_db_engine(database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    now = datetime.now(timezone.utc)

    n_accounts = 0
    n_videos = 0
    account_ids: dict[str, dict] = {}
    with factory() as session:
        for a in accounts:
            aid = account_id_for(a["platform"], a["account_name"],
                                 a["profile_url"])
            direct = is_direct_profile(a["platform"], a["profile_url"])
            status = normalise_verification(a.get("verification_status"),
                                            is_direct=direct)
            row = SocialAccount(
                account_id=aid,
                platform=a["platform"],
                account_name=a["account_name"][:200],
                handle=(a.get("handle") or "")[:160] or None,
                profile_url=a["profile_url"],
                channel_id=(a.get("channel_id") or None),
                account_type=(a.get("account_type") or "unknown")[:48],
                owner_entity=a.get("owner_entity"),
                coverage_scope=a.get("coverage_scope"),
                relevance=(a.get("relevance") or "")[:24] or None,
                verification_status=status[:32],
                ownership_evidence=a.get("ownership_evidence"),
                collectable_via=a.get("collectable_via"),
                discovery_method=(a.get("discovery_method") or "manual")[:48],
                discovery_evidence=a.get("discovery_evidence"),
                subscribers=a.get("subscribers"),
                video_count=a.get("video_count"),
                is_enabled=False,
                notes=a.get("notes"),
                first_seen_at=now,
                last_checked_at=now,
                origin_sheet=a.get("origin_sheet"),
                origin_row=a.get("origin_row"),
            )
            session.merge(row)
            n_accounts += 1
            account_ids[aid] = a

        # the videos, attached to their channel account
        seen_videos: set[str] = set()
        for ch in search.get("channels", []):
            cid = ch.get("channel_id")
            if not cid:
                continue
            # the account id must match the one used above
            aid = None
            for candidate_id, meta in account_ids.items():
                if meta.get("channel_id") == cid:
                    aid = candidate_id
                    break
            if aid is None:
                aid = account_id_for("youtube",
                                     ch.get("channel_name") or cid, cid)
            existing = session.get(SocialAccount, aid)
            if existing is None:
                continue
            for v in ch.get("videos", []):
                terms = v.get("bilaspur_terms") or []
                if not terms:
                    continue
                # the same video can surface under several queries, and
                # `merge` does not deduplicate on a unique key, so the
                # row is looked up first.
                if v["video_id"] in seen_videos:
                    continue
                seen_videos.add(v["video_id"])
                existing_video = session.scalar(
                    select(SocialVideo).where(
                        SocialVideo.platform == "youtube",
                        SocialVideo.video_id == v["video_id"]))
                if existing_video is not None:
                    existing_video.last_seen_at = now
                    n_videos += 1
                    continue
                row = SocialVideo(
                    account_id=aid,
                    platform="youtube",
                    video_id=v["video_id"],
                    url=v["url"],
                    title=(v.get("title") or "")[:2000] or None,
                    description=None,
                    published_label=v.get("published"),
                    views_label=v.get("views"),
                    match_evidence=(f"matched {', '.join(terms)} in the "
                                    f"title/channel name"),
                    match_terms=", ".join(terms),
                    discovered_via="youtube_search",
                    discovery_query=v.get("query"),
                    discovered_at=now,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                session.add(row)
                n_videos += 1
        session.commit()
    engine.dispose()
    return {"accounts": n_accounts, "videos": n_videos}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK))
    parser.add_argument("--search", default=str(ROOT / "data" / "youtube_search.json"))
    parser.add_argument("--channels", default=str(ROOT / "data" / "youtube_channels.json"))
    parser.add_argument("--out", default=str(ROOT / "data" / "social_accounts.json"))
    parser.add_argument("--database-url", default="sqlite:///data/news.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the dataset but do not write to SQLite")
    args = parser.parse_args()

    wb_path = Path(args.workbook)
    if not wb_path.exists():
        print(f"workbook not found: {wb_path}")
        return 1
    accounts = read_workbook(wb_path)
    print(f"workbook Platform Accounts: {len(accounts)} rows")

    search = {}
    if Path(args.search).exists():
        search = json.loads(Path(args.search).read_text(encoding="utf-8"))
        accounts = merge_search_results(accounts, search)
        print(f"after YouTube search merge: {len(accounts)} accounts")

    if Path(args.channels).exists():
        resolved = json.loads(Path(args.channels).read_text(encoding="utf-8"))
        accounts = merge_channel_resolution(accounts, resolved)
        print(f"after channel resolution merge: {len(accounts)} accounts")

    Path(args.out).write_text(json.dumps(accounts, ensure_ascii=False, indent=2),
                               encoding="utf-8")

    by_platform: dict[str, int] = {}
    for a in accounts:
        by_platform[a["platform"]] = by_platform.get(a["platform"], 0) + 1
    print("by platform:", by_platform)

    direct = sum(1 for a in accounts
                 if is_direct_profile(a["platform"], a["profile_url"]))
    print(f"direct profile URLs: {direct}/{len(accounts)}")

    if args.dry_run:
        print(f"\n(dry run) dataset written to {args.out}")
        return 0

    counts = load_dataset(accounts, search, args.database_url)
    print(f"\nloaded into {args.database_url}: "
          f"{counts['accounts']} accounts, {counts['videos']} videos")
    print(f"dataset: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
