"""End-to-end demo: the news scraper against a JavaScript-rendered site.

Runs the **real CLI code paths** against a localhost site whose news only
appears after JavaScript executes:

1. HTTP-only attempt  -> the listing is invisible, 0 articles (the proof
   that a plain fetch cannot read this site);
2. `verify-source --browser` -> D10 approval + budgeted browser
   verification;
3. `enable-source` -> operator approval;
4. `run --dry-run`  -> full execution, zero writes;
5. `run` (live)     -> articles stored with original Hindi text.

Usage:

    python scripts/demo_browser_run.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from js_demo_site import start_server  # noqa: E402

DB_PATH = Path(tempfile.gettempdir()) / "bilaspur_browser_demo.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"

from bilaspur_scraper.cli import main  # noqa: E402
from bilaspur_scraper.database.models import Source  # noqa: E402
from bilaspur_scraper.database.repositories import ArticleRepository  # noqa: E402
from bilaspur_scraper.database.session import (  # noqa: E402
    create_db_engine,
    create_schema,
    make_session_factory,
)
from bilaspur_scraper.pipeline.run import run_collection  # noqa: E402
from bilaspur_scraper.config.settings import load_settings  # noqa: E402


def seed_source(base: str) -> None:
    engine = create_db_engine(f"sqlite:///{DB_PATH}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(Source(
            source_id="js-demo-site",
            source_name="Bilaspur JS Demo Site",
            source_type="publisher_local",
            url=f"{base}/",
            url_kind="homepage",
            coverage_scope="district",
            priority="P1",
            collection_method="none",
            source_verification_status="unverified",
            activity_status="pending",
            lifecycle_state="candidate",
            enabled=False,
            notes="localhost demo: listing rendered by JavaScript",
            origin_sheet="demo",
            origin_row=0,
        ))
        session.commit()
    engine.dispose()


def show_articles() -> None:
    engine = create_db_engine(f"sqlite:///{DB_PATH}")
    factory = make_session_factory(engine)
    with factory() as session:
        rows = ArticleRepository(session).recent("js-demo-site", limit=10)
        print(f"\nSTORED ARTICLES: {len(rows)}")
        for row in rows:
            print(f"  [{row.bilaspur_relevance}] {row.title}")
            print(f"      {row.canonical_url}")
            print(f"      language={row.language} chars={len(row.content_text or '')} "
                  f"extraction={row.extraction_method} hash={row.content_hash[:18]}…")
    engine.dispose()


def main_demo() -> int:
    if DB_PATH.exists():
        DB_PATH.unlink()
    base, server = start_server()
    print(f"JS demo site: {base}")
    seed_source(base)

    settings = load_settings()
    engine = create_db_engine(f"sqlite:///{DB_PATH}")
    factory = make_session_factory(engine)
    with factory() as session:
        row = session.get(Source, "js-demo-site")

    print("\n--- 1. HTTP-only attempt (no browser approval) ---")
    report = run_collection(settings, row, dry_run=False, max_items=5)
    print(f"  strategy items={report.totals.feed_items} "
          f"written={report.totals.written} ok={report.ok}")
    print("  -> the listing is injected by JavaScript, so HTTP sees nothing")

    print("\n--- 2. verify-source --browser (D10 approval + budget) ---")
    main(["verify-source", "js-demo-site", "--browser", "--by", "demo operator"])

    print("\n--- 3. enable-source ---")
    main(["enable-source", "js-demo-site", "--by", "demo operator"])

    print("\n--- 4. run --dry-run (zero writes) ---")
    main(["run", "js-demo-site", "--dry-run", "--max-items", "5"])

    print("\n--- 5. run (live) ---")
    main(["run", "js-demo-site", "--max-items", "5"])

    show_articles()
    server.shutdown()
    print(f"\n(demo database: {DB_PATH})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_demo())
