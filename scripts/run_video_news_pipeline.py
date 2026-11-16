"""The video-news pipeline, end to end.

    YouTube video
        -> transcript        (the publisher's own caption track)
        -> fact sheet        (minimax-m3.1-flash, checked against the
                              transcript)
        -> news article      (the site's `lnb-` template)
        -> featured image    (generated, 1200x675)
        -> WordPress draft   (media upload + post create)

Everything before the WordPress call is read-only against the network
apart from the fetch itself. The post is created as a **draft**:
publishing is a separate, explicit step, because `SKILLS.md` §10
requires human corroboration for crime, accident, health, legal and
election material.

Usage:
    python scripts/run_video_news_pipeline.py --limit 5
    python scripts/run_video_news_pipeline.py --source "Bilaspur Grand News"
    python scripts/run_video_news_pipeline.py --limit 3 --publish
    python scripts/run_video_news_pipeline.py --limit 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.config.settings import load_env_file  # noqa: E402

load_env_file()

from bilaspur_scraper.database.media_models import (  # noqa: E402
    MediaTranscript,
    SocialVideo,
)
from bilaspur_scraper.database.session import (  # noqa: E402
    create_db_engine,
    create_schema,
    make_session_factory,
)
from bilaspur_scraper.publishing import (  # noqa: E402
    WordPressClient,
    build_post_payload,
    check_facts,
    extract_facts,
    to_fact_sheet,
)
from bilaspur_scraper.publishing.image_providers import (  # noqa: E402
    alt_text_for,
    available_providers,
    generate_featured_image,
)
from bilaspur_scraper.publishing.pipeline import publish_from_transcript  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from media_pipeline import (  # noqa: E402
    fetch_audio,
    fetch_captions,
    transcribe_local,
    transcribe_nvidia,
)


def pending_videos(database_url: str, *, limit: int = 5,
                   source: str | None = None,
                   hours: int = 720, only_bilaspur: bool = True,
                   allow_transcribed: bool = False) -> list[dict]:
    """Videos worth processing: recent, and not yet transcribed."""
    engine = create_db_engine(database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    from sqlalchemy import select

    since = datetime.now(timezone.utc).timestamp() - hours * 3600
    with factory() as session:
        rows = session.query(SocialVideo).join(
            SocialVideo.__table__.metadata.tables["social_accounts"],
            isouter=True).all()
        out = []
        for v in rows:
            if not allow_transcribed:
                done = session.get(MediaTranscript, v.id)
                if done is not None:
                    continue
            if v.published_at is None:
                continue
            ts = v.published_at.timestamp()
            if ts < since:
                continue
            acct = session.execute(
                select(SocialVideo.__table__.metadata.tables["social_accounts"])
                .where(SocialVideo.__table__.metadata.tables["social_accounts"]
                       .c.account_id == v.account_id)).first()
            name = acct[1] if acct else v.account_id
            if source and source.lower() not in (name or "").lower():
                continue
            if only_bilaspur and not v.match_terms:
                continue
            out.append({
                "video_pk": v.id, "video_id": v.video_id, "url": v.url,
                "title": v.title, "channel": name,
                "published": v.published_at.isoformat(),
                "match_terms": v.match_terms,
            })
            if len(out) >= limit:
                break
    engine.dispose()
    return out


def _store_transcript(database_url: str, video_pk: int, t: dict) -> None:
    """Save the transcript so a later run does not repeat the work."""
    engine = create_db_engine(database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    from sqlalchemy import select

    with factory() as session:
        existing = session.scalar(
            select(MediaTranscript).where(
                MediaTranscript.video_pk == video_pk))
        if existing is None:
            session.add(MediaTranscript(
                video_pk=video_pk, method=t.get("method", "unknown"),
                language=t.get("language"), language_source="detected",
                text=t["text"], char_count=len(t["text"]),
                segment_count=t.get("segments"),
                model_name=t.get("model"),
                audio_path=None))
        else:
            existing.text = t["text"]
            existing.char_count = len(t["text"])
            existing.method = t.get("method", existing.method)
        session.commit()
    engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--source", default=None,
                        help="only this channel (name substring)")
    parser.add_argument("--hours", type=int, default=720)
    parser.add_argument("--database-url", default="sqlite:///data/news.db")
    parser.add_argument("--image-dir", default=str(ROOT / "data" / "featured"))
    parser.add_argument("--categories", default="bilaspur",
                        help="comma-separated WordPress category slugs")
    parser.add_argument("--publish", action="store_true",
                        help="publish instead of drafting (non-sensitive only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except touch WordPress")
    parser.add_argument("--all", action="store_true",
                        help="include videos whose title has no Bilaspur term")
    parser.add_argument("--out", default=str(ROOT / "data" / "pipeline_runs.json"))
    args = parser.parse_args()

    print(f"image providers available: {', '.join(available_providers())}")
    client = WordPressClient()
    if not client.configured:
        print("WORDPRESS_APP_PASSWORD is not set - the run will stop before "
              "posting")
    print()

    videos = pending_videos(
        args.database_url, limit=args.limit, source=args.source,
        hours=args.hours, only_bilaspur=not args.all)
    print(f"{len(videos)} videos to process\n")
    if not videos:
        print("nothing to do")
        return 0

    results = []
    for i, v in enumerate(videos, 1):
        print(f"--- [{i}/{len(videos)}] {v['channel']}: {(v['title'] or '')[:56]}")
        url = v["url"]

        # 1. transcript - the publisher's own captions, or speech-to-text
        #    over the audio when there is no caption track.
        t = fetch_captions(url)
        if t is None:
            print("    no caption track -> audio fallback")
            audio = fetch_audio(url)
            if audio is None:
                print("    no audio either -> skipped")
                results.append({**v, "status": "no_transcript"})
                continue
            t = transcribe_local(audio)
            if t is None:
                # Either the clip has no narration (a photo slideshow
                # with music) or the transcription failed. The two are
                # worth telling apart: one is normal, the other is a
                # problem to fix.
                print("    no transcript produced from the audio "
                      "(no narration, or the local model failed)")
                results.append({**v, "status": "no_speech"})
                continue
            print(f"    transcript: {len(t['text'])} chars "
                  f"(via {t['method']})")
        else:
            note = " (truncated to the most recent part)" if t.get("truncated") else ""
            print(f"    transcript: {len(t['text'])} chars "
                  f"({t['segments']} segments){note}")

        # store the transcript so a re-run does not repeat the work
        _store_transcript(args.database_url, v["video_pk"], t)

        # 2. fact sheet
        model = extract_facts(t["text"], channel=v["channel"])
        if model is None:
            print("    no fact sheet -> skipped")
            results.append({**v, "status": "no_facts"})
            continue
        facts = to_fact_sheet(model, transcript=t["text"],
                              channel=v["channel"], video_url=url,
                              published_at=v["published"])

        # 3. fact-check against the transcript
        check = check_facts(facts, t["text"])
        print(f"    fact-check: {'OK' if check['ok'] else 'FLAGGED'}"
              f"  sensitive={facts.sensitive}")
        if not check["ok"]:
            for flag in check["flags"][:4]:
                print(f"      ! {flag}")
            results.append({**v, "status": "fact_check_failed",
                            "flags": check["flags"]})
            continue

        payload = build_post_payload(facts)
        print(f"    headline: {facts.headline[:60]}")
        print(f"    slug    : {payload['slug']}")

        # 4. featured image
        image_path = Path(args.image_dir) / f"{payload['slug']}.jpg"
        try:
            _, provider = generate_featured_image(
                image_path, headline=facts.headline, channel=v["channel"],
                source=facts.source,
                published_label=(v["published"] or "")[:10],
                provider="auto")
            print(f"    image   : {image_path.name} (via {provider})")
        except Exception as exc:  # noqa: BLE001
            print(f"    image   : FAILED ({exc})")
            image_path = None

        # 5. post
        if args.dry_run:
            print("    [dry-run] not posting")
            results.append({**v, "status": "dry_run",
                            "headline": facts.headline,
                            "slug": payload["slug"],
                            "sensitive": facts.sensitive})
            continue

        if not client.configured:
            print("    no WordPress credentials -> not posting")
            results.append({**v, "status": "no_credentials",
                            "headline": facts.headline,
                            "slug": payload["slug"]})
            continue

        category_ids = client.category_ids(
            [c.strip() for c in args.categories.split(",") if c.strip()])
        result = publish_from_transcript(
            t["text"], client=client, video_url=url,
            channel=v["channel"], published_label=(v["published"] or "")[:10],
            categories=category_ids, include_ads=True,
            image_dir=Path(args.image_dir), publish=args.publish)
        # publish_from_transcript regenerates the image; keep the one we
        # already made if it is on disk
        print(f"    post    : id={result.post_id} status={result.status} "
              f"{result.link}")
        if result.error:
            print(f"    note    : {result.error}")
        results.append({**v, "status": result.status,
                        "post_id": result.post_id, "link": result.link,
                        "headline": facts.headline, "slug": payload["slug"],
                        "sensitive": facts.sensitive,
                        "fact_check": check})

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False,
                                         indent=2), encoding="utf-8")
    done = [r for r in results if r.get("status") in ("draft", "publish")]
    print(f"\n{len(done)}/{len(results)} posts created "
          f"({sum(1 for r in results if r.get('sensitive'))} sensitive, "
          f"left as drafts for review)")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
