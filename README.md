# Bilaspur News Scraper v1

News **collection and storage engine** for Bilaspur, Chhattisgarh — collection,
extraction, normalization, validation, and storage only. It never publishes,
rewrites, or translates anything.

**Status:** Phases 0–1 implemented and proven against real sources. The
registry, verification engine (budgeted, evidence-first), and collection
pipeline (feed → sitemap → listing per D11) all work. First live articles
are collected; 129 tests pass. See `PLAN.md` and `ARCHITECTURE.md`.

## Documents

| File | Role |
|---|---|
| `SKILLS.md` | Governing charter (kept outside the repo by the owner) |
| `ARCHITECTURE.md` | Detailed design of record (contracts, lifecycle, error taxonomy) |
| `PLAN.md` | Implementation roadmap and phased build order |

## What exists

- `src/bilaspur_scraper/schemas/` — canonical enums + Pydantic contracts
  (`SourceRecord`, `ArticleDraft`, `ArticleRecord`).
- `src/bilaspur_scraper/registry/` — workbook loader, URL/classification rules,
  lifecycle state machine (8 states, strict transitions), alias resolution.
- `src/bilaspur_scraper/database/` — SQLAlchemy 2.x models, SQLite session
  (WAL, FK enforcement), repository layer (the only SQL-touching module).
- `src/bilaspur_scraper/config/` — env-driven settings; contact email lives
  **only** in the local, untracked `.env`; live crawls refuse without it.
- `src/bilaspur_scraper/fetching/` — `FetchManager` (single retry authority on
  Crawlee's `HttpxHttpClient`, redirects/retries counted), `RequestBudget`,
  `RobotsGate` (protego), and `BrowserFetcher` (Chromium, D10) behind the
  same `FetchResult` contract for per-source approved sources.
- `src/bilaspur_scraper/verification/` — `VerificationManager`: hard
  10-request budget (D9, redirects + retries counted), robots → homepage →
  feeds → sitemap → listing order, outcomes `verified` / `rejected` /
  `disabled` (budget stop never rejects), full evidence records (D17),
  zero-request listing completion after budget exhaustion (approved
  amendment), source coverage-corroborated relevance.
- `src/bilaspur_scraper/discovery/` — feeds (feedparser), sitemaps, listing
  links; the D11 discovery order with fall-through.
- `src/bilaspur_scraper/extraction/` — layered fill-missing extraction
  (JSON-LD/OG → semantic DOM → trafilatura), confidence gates (title ≥ 8
  chars, body ≥ 300 chars), raw bodies in memory only (D13).
- `src/bilaspur_scraper/processing/` — language detection (D12), normalizer,
  rule-based relevance (Keyword Watchlist driven, Chhattisgarh co-occurrence
  guard, source-coverage corroboration, sensitive-topic gate), dedup
  (canonical URL + content hash).
- `src/bilaspur_scraper/pipeline/` — `CollectionPipeline`: enabled-only,
  dry-run = zero writes (D5), live = run ledger + per-article transactions +
  health.
- `src/bilaspur_scraper/cli.py` — `init-db`, `import-registry`, `show-sources`,
  `verify-source`, `enable-source`, `run`, `health`.

## Guarantees enforced

- Every workbook row starts as **candidate**; `verify-source` decides with
  recorded evidence; `verified → enabled` requires an operator name (audit
  trail) and is state-machine enforced.
- Google-search stub rows and link hubs → `excluded`; never fetched.
- Import is idempotent and never downgrades an advanced lifecycle state.
- The 10-request verification budget counts redirects and retries; exhaustion
  → `disabled`, never `rejected`; only zero-request analysis may finish after.
- Dry-runs execute fully but write **nothing** (D5); only live runs touch
  article tables, and only for `enabled` sources.
- Original Hindi/English text is preserved verbatim (revision 1);
  metadata-only persistence (D13).

## Usage

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests -q

python -m bilaspur_scraper.main init-db
python -m bilaspur_scraper.main import-registry "C:\path\to\bilaspur_media_public_figures_directory_updated.xlsx"
python -m bilaspur_scraper.main show-sources

# networked verification (requires USER_AGENT/CONTACT_EMAIL in .env)
python -m bilaspur_scraper.main verify-source district-administration-bilaspur

# operator approval (audit trail)
python -m bilaspur_scraper.main enable-source district-administration-bilaspur --by "<operator>"

# collection: dry-run first (zero writes), then live
python -m bilaspur_scraper.main run district-administration-bilaspur --dry-run
python -m bilaspur_scraper.main run district-administration-bilaspur

python -m bilaspur_scraper.main health
```

## Pilot results (2026-11-12, real network)

- **Verified:** district-administration-bilaspur (listing, 10/10 budget),
  bilaspur-live, the-bilasa-times, takhatpur-ki-bhadas,
  raja-sahab-news-agency (all RSS, 2 requests each).
- **Enabled + collected live:** 20 articles from 5 sources; original Hindi
  text preserved; relevance + sensitive flags recorded; run ledger + health
  written.
- **Disabled (retry later):** bilaspur-police and SECL (robots.txt 403 — WAF),
  district-election-officer and bilaspur-district-court (weak listing, budget
  exhausted).
- **Rejected:** bilaspur-municipal-corporation (1 link), chhattisgarh-cmo,
  GGV, ABVV (no feeds/links on registered pages) — re-openable with better
  URLs per D15.
- **Deliberately skipped:** zila-panchayat-bilaspur (registry URL is the
  district-portal placeholder; needs the real page first).

## Live sweep (2026-11-15, real network)

- **Collected:** bilaspur-live, takhatpur-ki-bhadas, the-bilasa-times and
  raja-sahab-news-agency — 27 articles, 27 revisions, 12 runs. Repeated runs
  update in place instead of duplicating.
- **Failed honestly:** district-administration-bilaspur (`E_ROBOTS_UNAVAILABLE`
  — bilaspur.gov.in robots.txt timed out); raja-sahab-news-agency served
  HTTP 200 with empty bodies (verified site-side — the site is serving blank
  HTML while its RSS feed still works).
- **Browser adapter:** `scripts/demo_browser_run.py` proves the Chromium path
  against a JS-rendered localhost site (HTTP sees 0 items, the browser sees
  3, and the Hindi text is stored verbatim).

## Dashboard (read-only monitor)

```powershell
python scripts/dashboard_server.py --port 8766
```

Then open <http://127.0.0.1:8766>. It shows the collected articles with their
original text, relevance verdicts and sensitive-topic flags, plus the source
registry, per-source health and the run ledger. Search and filter by source,
relevance or sensitive topic; click an article to read the full body.

**The dashboard is strictly read-only.** It opens the database with
`mode=ro`, so SQLite refuses any write, and it never runs a collection —
triggering a crawl stays a deliberate CLI action. An open dashboard tab
therefore cannot cause outbound traffic to any publisher.

## Not in v1 (deliberately)

No AI summarization, no social media collection, no publishing,
no scheduler. Browser crawling only with per-source explicit approval (D10).
