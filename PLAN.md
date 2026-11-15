# PLAN.md — Bilaspur News Scraper (v1)

**Status:** Implementation roadmap, reconciled with the approved architecture. **Not implemented.**
**Date:** 2026-11-11
**Project:** Local News Bilaspur — source ingestion
**Governing document:** `SKILLS.md` (Bilaspur News Scraper charter)
**Detailed design of record:** `ARCHITECTURE.md` (contracts, lifecycle state machine, component boundaries, error taxonomy, operational rules — referenced here, not duplicated)
**Inputs:** `bilaspur_media_public_figures_directory_updated.xlsx` (source registry)

> **File locations:** `SKILLS.md` and the workbook were inspected from `C:\Users\Asus\Downloads\` and are **not present in this project folder**. Only this `PLAN.md` lives in the project. If either governing file is moved into the project later, update this note.
>
> This document is the implementation plan. It contains no code. It is derived directly from `SKILLS.md` and the actual contents of the Excel workbook, not from assumptions.

---

## Table of contents

1. Purpose and scope
2. Locked decisions
3. How `SKILLS.md` is used
4. Source inventory (grounded in the workbook)
5. Canonical registry schema
6. Pipeline architecture and module map
7. Relevance classification specification
8. Deduplication specification
9. Extraction and normalization rules
10. Source health tracking
11. Database schema (logical)
12. Configuration and secrets
13. Testing strategy
14. Phased build order
15. Risks and open questions
16. Out of scope for v1
17. Definition of done
18. Appendix — workbook-to-system mapping

---

## 1. Purpose and scope

Build a **deterministic, polite, provenance-first ingest pipeline** that:

- reads the Excel source registry,
- discovers public articles/notices from **websites and RSS/Atom only**,
- extracts and normalizes fields,
- classifies Bilaspur relevance using explicit rules,
- deduplicates and stores clean, traceable records for **human editorial review**.

The pipeline never writes, rewrites, translates, or publishes news. It stops at a validated, stored record with `record_status = needs_review` or `collected`.

**v1 platform scope:** websites + RSS/Atom only. Social platforms, public figures, and the discovery backlog are explicitly deferred.

---

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Build style | Deterministic pipeline (Crawlee for Python + Pydantic) | Testable; matches `SKILLS.md` §13/§14 |
| Framework | Crawlee for Python | Mandated by `SKILLS.md` §5 |
| Application shape | **Modular monolith** (ARCHITECTURE.md D1) | One Python app, one SQLite DB, explicit module contracts |
| System split | **Control Plane vs Collection Pipeline** (ARCHITECTURE.md D3) | Unverified sources can never enter normal collection |
| Crawling strategy | **HTTP-first** (`HttpCrawler`); Playwright fallback only with per-source approval (ARCHITECTURE.md D2, D10) | Lower cost; browser use is a deliberate, evidenced exception |
| Verification budget | **Hard 10 outbound requests per attempt**; counts redirects + retries; one host; exhaustion → `disabled`, never `rejected`; zero-request completion of the listing check on the already-fetched homepage is permitted after exhaustion (ARCHITECTURE.md D9 + approved amendment) | Keeps verification controlled and auditable |
| Dry-run | **Logs only — zero database writes of any kind**; `enabled` sources only (ARCHITECTURE.md D5) | Dry-run never grants access to unverified sites |
| Retry authority | **Crawlee is the single HTTP retry authority** (ARCHITECTURE.md D8, §10) | No double-retry across layers |
| Language detection | **Script-based**: Devanagari → `hi`, Latin → `en`, mixed/weak → `und` (ARCHITECTURE.md D12) | Deterministic; no forced classification |
| Raw responses | **Metadata only in production**; no raw HTML persisted; sanitized fixtures for tests (ARCHITECTURE.md D13) | Provenance without bulk storage |
| v1 platforms | Websites + RSS/Atom | User decision; social not reliably scrapable without official APIs |
| Relevance | Rule-based (no LLM) | Keyword Watchlist is sufficient; keeps output testable |
| Database | SQLite for v1; PostgreSQL is a future migration option only | Single-user, manual ingestion; zero server setup. Schema avoids SQLite-specific coupling (see §11.1) |
| Persistence | SQLAlchemy 2.x + repository layer (ARCHITECTURE.md D7) | Portable schema; repositories hide SQL |
| Source activation | Manual verification gate; a registry row is never auto-enabled | `SKILLS.md` §3.1, §15; see §4.5 |
| Initial pilot | Three institutional candidates: District Administration, Bilaspur Police, SECL | Controlled rollout; see §4.6 |
| Scheduler | Manual CLI only in v1; scheduling is a separate optional milestone | Prove manual runs are correct and idempotent before any automatic cadence |
| Language | Preserve original (Hindi + English), no translation | `SKILLS.md` §7, §16 |
| Publishing | None. Ever, in v1 | `SKILLS.md` §1, §10, §16 |

---

## 3. How `SKILLS.md` is used

`SKILLS.md` is split into three artifacts. It is **not** loaded as an agent prompt.

| `SKILLS.md` part | Becomes | Enforced by |
|---|---|---|
| §4, §6, §11, §12 field lists and enums | Pydantic record model + DB columns | Runtime validation |
| §2, §7, §10, §16 "must/must not" | Validators and policy gates | Reject / quarantine / `needs_review` |
| §5, §9, §13, §14 conventions | Module layout and test plan | Project structure + CI |
| §1, §3, §15 prose | Human documentation | README |

**Rule:** a scraped item is a **lead**, never a verified fact (`SKILLS.md` §2.9). The scraper records verification state; it never promotes it.

---

## 4. Source inventory (grounded in the workbook)

The workbook is the authoritative **candidate** registry. Its own README states it is "not a complete census or a live verification certificate." Every row is treated as a candidate.

### 4.1 Media Publishers — 36 rows, classified

URL quality was checked row by row. Only some are crawlable as-is.

| # | Publisher | Registry URL | URL class | Handling (after verification) |
|---|---|---|---|---|
| 1 | The Bilasa Times | thebilasatimes.in | CRAWL | Verify RSS/listing, then crawl |
| 2 | Bilaspur Live | blive.news | CRAWL | Verify, then crawl |
| 3 | Takhatpur Ki Bhadas / Bilaspur Ki Bhadas | bilaspurkibhadas.com | CRAWL | Verify, then crawl |
| 4 | Raja Sahab News Agency | rajasahabnews.in | CRAWL | Verify (candidate status) |
| 5 | Chhattisgarh Janta Ki Awaaz News | chhattisgarhjantakiawaaznews.online | CRAWL | Verify ownership/activity |
| 6 | Janta Ki Aawaz | jantakiaawaz.in | CRAWL | Verify cadence |
| 7 | Nishaanebaz | nishaanebaz.com | CRAWL | Verify |
| 8 | Live TV Chhattisgarh | livetvchhattisgarh.com | CRAWL | Verify active in window |
| 9 | Glibs.in | glibs.in | CRAWL | Verify Bilaspur relevance |
| 10 | Intv | intv.ueniweb.com | CRAWL | Verify |
| 11 | Jungle Book | junglebook-1.ueniweb.com | CRAWL | Verify |
| 12 | Nai Dunia – Bilaspur | naidunia.com/chhattisgarh/bilaspur | SECTION | Crawl section only |
| 13 | Vistaar News – Bilaspur | vistaarnews.com/category/chhattisgarh/bilaspur/ | SECTION | Crawl section only |
| 14 | NDTV MPCG | mpcg.ndtv.in/chhattisgarh-news/bilaspur | SECTION | Crawl section only |
| 15 | Amar Ujala – Bilaspur | amarujala.com/video/chhattisgarh/bilaspur-chhattisgarh | SECTION | Video section; verify |
| 16 | Haribhoomi – Bilaspur | haribhoomi.com/topic/bilaspur-news | SECTION | Crawl topic only |
| 17 | Live Hindustan – Bilaspur | livehindustan.com/chhattisgarh/bilaspur/news | SECTION | Crawl section only |
| 18 | Dainik Bhaskar | bhaskar.com | DISCOVER | Find Bilaspur section first |
| 19 | Patrika | patrika.com | DISCOVER | Find Bilaspur section first |
| 20 | Deshbandhu | deshbandhu.co.in | DISCOVER | Find Bilaspur section first |
| 21 | IBC24 | ibc24.in | DISCOVER | Find Bilaspur section first |
| 22 | INH News | linktr.ee/inhnews | AGGREGATOR | Resolve links; not a source |
| 23 | Sindhu Youth Media | google.com/search?... | EXCLUDE | Park as search_stub |
| 24 | Samay News Network | google.com/search?... | EXCLUDE | Park as search_stub |
| 25 | CG24 News Channel | google.com/search?... | EXCLUDE | Park as search_stub |
| 26 | TV 24 (Bilaspur office) | google.com/search?... | EXCLUDE | Park as search_stub |
| 27 | Bilaspur Update | google.com/search?... | EXCLUDE | Park as search_stub |
| 28 | Nutan Bharat TV | google.com/search?... | EXCLUDE | Park as search_stub |
| 29 | IDP 24 News Satellite Channel | google.com/search?... | EXCLUDE | Park as search_stub |
| 30 | Expose24 Plus | google.com/search?... | EXCLUDE | Park as search_stub |
| 31 | Nation Update | google.com/search?... | EXCLUDE | Park as search_stub |
| 32 | News Network India | google.com/search?... | EXCLUDE | Park as search_stub |
| 33 | Just 36 News | google.com/search?... | EXCLUDE | Park as search_stub |
| 34 | MKN News Channel | google.com/search?... | EXCLUDE | Park as search_stub |
| 35 | Asian News Bharat | google.com/search?... | EXCLUDE | Park as search_stub |
| 36 | Hello Media | google.com/search?... | EXCLUDE | Park as search_stub |

**Tally:** 11 CRAWL · 6 SECTION · 4 DISCOVER · 1 AGGREGATOR · 14 EXCLUDE.

**Important:** these classes describe **how a URL would be handled**, not activation. Every publisher row starts as `candidate`; none is crawled until it passes verification (§4.5).

**Important:** the 14 Google-search rows are **not sources**. They are leads. They must be stored as `url_kind = search_stub`, `lifecycle_state = excluded`, `enabled = false`, `activity_status = not_collectable_via_current_method`. The scraper must never request them as news sources, never treat search-result pages as publisher listings, never follow search results to create enabled sources, and never treat a Google result as verification. They remain available for a separate **human-led** source-discovery process; a genuine publisher URL found later is added as a new `discovered` record and must pass normal verification.

### 4.2 Institutions & Business — 12 rows, all candidate targets

| # | Organisation | Type | Registry URL | Status before verification |
|---|---|---|---|---|
| 1 | District Administration, Bilaspur | District government | bilaspur.gov.in | **PILOT CANDIDATE** |
| 2 | Bilaspur Police | Police department | bilaspur.cgpolice.gov.in | **PILOT CANDIDATE** |
| 3 | District Election Officer, Bilaspur | Election office | bilaspur.gov.in/en/deo-portal/ | CANDIDATE — inactive until verified |
| 4 | South Eastern Coalfields Limited (SECL) | Public-sector company | secl-cil.in | **PILOT CANDIDATE** |
| 5 | Guru Ghasidas Vishwavidyalaya | Central university | ggu.ac.in | CANDIDATE — inactive until verified |
| 6 | Atal Bihari Vajpayee Vishwavidyalaya | State university | abvv.ac.in | CANDIDATE — inactive until verified |
| 7 | Bilaspur Municipal Corporation | Urban local body | bilaspur.gov.in/en/public-utility-category/urban-body/ | CANDIDATE — inactive until verified |
| 8 | Zila Panchayat Bilaspur | District local government | bilaspur.gov.in | CANDIDATE — inactive until verified |
| 9 | Bilaspur District Court | Judiciary | bilaspur.dcourts.gov.in | CANDIDATE — inactive until verified |
| 10 | Indian Industries Association – Bilaspur | Industry association | iiabilaspur.org | CANDIDATE — inactive until verified |
| 11 | Chhattisgarh Chamber of Commerce & Industries | Trade association | cgchamber.org | CANDIDATE — inactive until verified |
| 12 | Chhattisgarh CMO | State government office | cgstate.gov.in | CANDIDATE — inactive until verified |

These are the **highest-value, lowest-risk** v1 targets: official, authoritative, and primary-source. **A URL in the workbook is not proof that the site works.** Every one of the 12 must be imported, assigned an explicit verification status, and pass live verification before it is enabled. Only verified sources are crawled.

- The **three pilot candidates** (#1 District Administration, #2 Bilaspur Police, #4 SECL) enter the initial verification pilot (§4.6).
- The **other nine remain registered but inactive**, and are enabled only after separate verification and approval.
- No institutional source is currently verified or enabled. No claim is made here that any of these sites is accessible, has a working RSS/Atom feed, or will pass verification.

**Host-sharing note:** rows 1, 3, 7, 8 all live on `bilaspur.gov.in`. Keep **separate source_ids** but use a **shared per-host rate limiter and fetch cache** to avoid hitting the same domain multiple times per run.

### 4.3 Deferred source groups (schema-ready, inactive in v1)

| Sheet | Rows | Disposition |
|---|---|---|
| Platform Accounts | 46 | Social. Stored with `enabled = false`. |
| Public Figures | 9 | Social/statement sources. Deferred. |
| Additional Monitoring Targets | 61 | Discovery backlog for P3/P4. Not crawled. |
| Verification Queue | 10 | **Human work.** Mirrored read-only; never executed by the scraper. |
| Research References | 15 | Evidence only. Not crawl targets. |

### 4.4 Realistic v1 fleet expectation

| Bucket | Count |
|---|---|
| Institution candidates | 12 (3 in initial pilot; 9 inactive until verified) |
| Local publisher sites | 11 |
| Regional sections | 6 |
| Regional homepages needing discovery | 4 |
| **Total candidate crawl targets** | **33** |
| Expected (not promised) to pass verification | **~15–20** |
| Parked as search stubs / aggregator | 15 |

### 4.5 Source lifecycle states (consistent terminology)

Every registry entry occupies exactly one lifecycle state. The state machine and its guards are defined in full in `ARCHITECTURE.md` §4; the summary below is the operational reference.

| State | Meaning | May be crawled? |
|---|---|---|
| `candidate` | Imported from the workbook; identity/access not yet checked | No |
| `discovered` | A genuine publisher/institution URL has been identified (e.g. resolved from a stub or aggregator) | No, until verified |
| `verification_pending` | Verification authorized and in progress (under the 10-request budget) | Verification requests only |
| `verified` | Identity, access conditions, and collection method confirmed with recorded evidence | No, until enabled |
| `enabled` | Verified **and** approved; active in the crawl set | Yes |
| `disabled` | Previously enabled, paused — or verification ended inconclusively (incl. budget exhaustion) | No |
| `rejected` | Verification failed; **not terminal** — operator-authorized reopening only | No |
| `excluded` | Not a source: search stub, aggregator, or otherwise not collectable by current method | No |

**Strict transitions** (enforced; full guard table in `ARCHITECTURE.md` §4.2–4.3):

- Main path: `candidate → discovered → verification_pending → verified → enabled`.
- A source may move to `disabled` or `excluded` at any time.
- **A source may never skip `verified` and an evidence row.**
- `rejected` is **not terminal**: an operator may return it to `discovered` with a recorded audit reason when new evidence justifies re-verification (`rejected → discovered → verification_pending → …`).
- Budget exhaustion during verification → `disabled`/pending, **never `rejected`**.
- Forbidden: `candidate → enabled`, `discovered → enabled`, `verification_pending → enabled`, any → `enabled` without `verified_by` and evidence, `excluded → enabled` without a new `discovered` record.

**Evidence requirement:** each `verified`/`rejected` decision records `verification_evidence` (URL or fixture reference), `verification_checked_at` (timestamp), `verification_method` (rss / sitemap / listing / manual), HTTP status, robots outcome, `requests_consumed`, `request_budget`, `browser_used`, `verification_policy_version`, and the reviewer. Verification is never inferred from a URL's presence in the workbook.

### 4.6 Initial three-source pilot

The implementation begins with a controlled pilot of three institutional candidates. They are **pilot candidates, not verified or enabled sources**. No evidence currently exists that any of them is accessible, has a feed, or will pass verification.

| Slot | Source | Registry URL | Starting state |
|---|---|---|---|
| P1 | District Administration, Bilaspur | `bilaspur.gov.in` | `candidate` |
| P2 | Bilaspur Police | `bilaspur.cgpolice.gov.in` | `candidate` |
| P3 | South Eastern Coalfields Limited (SECL) | `secl-cil.in` | `candidate` |

The remaining nine institutional candidates stay `candidate`/`disabled` until independently verified and approved. The three-source pilot is an **implementation strategy, not a permanent source limit**; expansion follows §5.1.

**Pilot sequence (per `ARCHITECTURE.md` §5.4):** `verify-source` (hard 10-request budget) → operator approval → `run --source <id> --dry-run` (logs only, zero writes) → `run --source <id>` (live).

**Pilot acceptance criteria (per source, all required before `enabled`):**

1. Source URL and identity verified against an authoritative reference.
2. `robots.txt` and access conditions checked; crawl-delay honoured.
3. RSS/Atom feed discovery attempted first.
4. Sitemap discovery attempted where appropriate.
5. Listing-page discovery used only when feeds/sitemaps are unavailable (and, on normal runs, only as a bounded diagnostic cross-check — feed-only when the feed is healthy, per `ARCHITECTURE.md` D11).
6. A suitable extraction strategy identified and documented.
7. At least one real article extracted **where the source provides accessible article content**.
8. Extracted records pass Pydantic validation.
9. Source-specific parser tests exist (fixtures for the enabled source).
10. Verification evidence recorded with a timestamp, `requests_consumed` ≤ `request_budget` (10), `browser_used`, and `verification_policy_version` (§4.5).
11. Source is set to `enabled` only after criteria 1–10 pass.

**Failure rule:** if a source fails any criterion, record the failure and leave it `disabled`. Never weaken validation, relax the relevance guard, or fabricate a result to force a pass.

---

## 5. Canonical registry schema

The Excel rows are transformed **once** into a normalized `sources` table. This is the single source of truth the crawler reads.

| Field | Type | Notes |
|---|---|---|
| `source_id` | text (slug) | Stable, human-readable, e.g. `the-bilasa-times` |
| `source_name` | text | From registry, verbatim |
| `source_type` | enum | district_government, police, election_office, psu, central_university, state_university, urban_local_body, district_local_government, judiciary, industry_association, trade_association, state_government, publisher_local, publisher_regional, publisher_national, broadcaster, aggregator, lead |
| `platform` | enum | website, rss (social values reserved) |
| `url` | text | Crawl entry point (must be a real URL, not a search URL) |
| `url_kind` | enum | homepage, section, topic, video_section, sub_path, aggregator, search_stub |
| `coverage_scope` | enum | city, district, nearby, statewide, regional, national |
| `priority` | enum | P1, P2, P3, P4 (from Monitoring Priorities) |
| `collection_method` | enum | rss, sitemap, html, json_ld, api, none |
| `source_verification_status` | enum | verified_official, publisher_linked, portal_linked, search_link_only, unverified |
| `activity_status` | enum | active, active_unknown_cadence, pending, blocked, parser_broken, inactive, not_collectable_via_current_method |
| `lifecycle_state` | enum | candidate, discovered, verification_pending, verified, enabled, disabled, rejected, excluded (§4.5; `ARCHITECTURE.md` §4) |
| `enabled` | boolean | True **only** when `lifecycle_state = enabled`; never set by import |
| `verification_method` | enum | rss, sitemap, listing, manual |
| `verification_evidence` | text | URL, fixture reference, or note supporting the verification decision |
| `verification_checked_at` | timestamp | When verification was last performed |
| `verified_by` | text | Operator/reviewer who approved the state |
| `notes` | text | Registry notes preserved verbatim |
| `origin_sheet` | text | Source sheet name |
| `origin_row` | integer | Traceability back to workbook |
| `first_seen_at` | timestamp | When imported |
| `last_checked_at` | timestamp | Last verification/fetch attempt |

**Entity resolution:** several entities appear in multiple sheets under slightly different names (e.g., "Takhatpur Ki Bhadas / Bilaspur Ki Bhadas", "District Administration, Bilaspur" vs "Bilaspur District Administration / Collectorate"). A one-to-one mapping table (`source_aliases`) links registry rows to a single canonical `source_id` so the same publisher is never double-counted.

### 5.1 Adding a new source after v1

The system is **not** designed around a fixed list of three sources. After v1 begins, a new source is added through configuration plus, where necessary, a source-specific parser — without redesigning the pipeline.

1. Identify the genuine publisher or institution.
2. Record its canonical website URL.
3. Determine its `source_type`.
4. Check `robots.txt` and access conditions.
5. Discover RSS/Atom, sitemap, or listing pages (in that order).
6. Identify whether an existing parser can handle it.
7. Create a source-specific parser only when necessary.
8. Test extraction against real examples and saved fixtures.
9. Validate the resulting records with the Pydantic model.
10. Record verification evidence and timestamp.
11. Enable the source only after approval.

Adding a source must never require editing the pipeline core. Source-specific selectors and parsing rules live in source configuration or a dedicated handler (`SKILLS.md` §14).

---

## 6. Pipeline architecture and module map

The system is split into a **Control Plane** and a **Collection Pipeline** (`ARCHITECTURE.md` §2–§3). Only `enabled` sources enter the Collection Pipeline; the Verification Manager is the only component permitted to touch non-enabled sources, under its own budget.

```text
CONTROL PLANE:   registry · verification manager · configuration · run manager
                                  │ eligible (enabled) sources only
COLLECTION:      discovery → fetcher → parser → normalizer
                 → relevance → dedup → validator → persistence → health
```

| Module | Responsibility | SKILLS.md |
|---|---|---|
| `registry` | Excel → normalized `sources`; validate URL kinds; quarantine search stubs; **never auto-enable**; record lifecycle + verification | §3 |
| `verification` | Budgeted `verify-source` workflow (Control Plane): evidence, lifecycle transitions, never article writes | §15 |
| `run manager` | CLI orchestration, run context, dry-run/live mode, recovery of interrupted runs | §14 |
| `discovery` | RSS/Atom first; **feed-only on normal runs when a feed is available** (D11); then sitemap, then listing; skip `excluded`/`search_stub`; per-source strategy | §5.1–5.3 |
| `fetcher` | Crawlee `HttpCrawler` (browser fallback only per D10) with politeness, ETag/Last-Modified, robots.txt; **Crawlee is the single retry authority — application code never re-issues a fetch** | §9 |
| `parser` | Per-source handlers; feedparser; layered **fill-missing** extraction (structured → semantic DOM → handler → trafilatura) returning a single `ArticleDraft` (`ARCHITECTURE.md` §7.3, §13) | §5.2–5.3 |
| `normalizer` | Whitespace, Hindi Unicode, dates → UTC, **script-based language detection** (`hi`/`en`/`und`) | §7 |
| `relevance` | Rule engine: keyword groups + geography tiers + false-positive guard + sensitive flag | §6 |
| `dedup` | Five signals; revisions instead of duplicates | §8 |
| `validator` | Pydantic record model = §11 contract | §11 |
| `persistence` | Transactional upsert; URL uniqueness per source; revision + relation tables; SQLAlchemy 2.x repositories | §11 |
| `health` | Per-run source metrics | §12 |
| `cli` | `run`, `run --source`, `verify-source`, `health`, `--dry-run` | §14 |

**Cross-cutting requirements** (`SKILLS.md` §14):
- dry-run mode = **logs only, zero database writes of any kind** (`ARCHITECTURE.md` D5),
- idempotent re-runs,
- structured logs with stable error codes (canonical error taxonomy in `ARCHITECTURE.md` §10),
- secrets via environment variables only.

---

## 7. Relevance classification specification

Built directly from the workbook's **Keyword Watchlist** (13 groups) and **Monitoring Priorities** editorial gate. Deterministic; no LLM.

### 7.1 Geography tiers

| Tier | Terms (English + Hindi) | Effect |
|---|---|---|
| City | Bilaspur, बिलासपुर, Bilaspur Chhattisgarh, बिलासपुर छत्तीसगढ़ | Strongest signal |
| Locality | Sarkanda, सरकंडा, Torwa, तोरवा, Vyapar Vihar, व्यापार विहार, Seepat, सीपत, Koni, कोनी, Mopka, मोपका | Strong local signal |
| Nearby | Kota, कोटा, Takhatpur, तखतपुर, Masturi, मस्तूरी, Ratanpur, रतनपुर, Malhar, मल्हार | District signal |
| Institutions | SECL, CIMS, GGV, GGVU, ABVV, SECR, NTPC Sipat, जिला प्रशासन, नगर निगम, बिलासपुर पुलिस | Institutional signal |

### 7.2 Topic groups (from Keyword Watchlist)

public safety · civic issues · government & development · education · healthcare · transport & railway · environment & weather · business & employment · events & sports. Each group carries its English and Hindi terms and is stored as `matched_topics`.

### 7.3 False-positive guard (mandatory)

"Bilaspur" is ambiguous (Bilaspur HP, Bilaspur UP exist). A bare "Bilaspur" match scores **High/Medium only when** at least one Chhattisgarh signal co-occurs: `Chhattisgarh`, `छत्तीसगढ़`, `CG`, a list of district localities, or a Bilaspur institution. Otherwise → **Unknown** with reason `"ambiguous place name; no Chhattisgarh signal"`.

A keyword match alone is never proof of relevance (`SKILLS.md` §6).

### 7.4 Scoring rules

| Output | Condition |
|---|---|
| **High** | Explicit city/locality event or institution; or nearby-area item with district context |
| **Medium** | Chhattisgarh/regional item with clear material impact on Bilaspur; or institution action affecting Bilaspur |
| **Low** | Incidental mention, list-only, or no clear local impact |
| **Unknown** | Content unavailable/too short/ambiguous, or failed the guard |

Every record stores `bilaspur_relevance`, `relevance_reason`, and `matched_terms` (exact phrases that fired). This makes classification auditable and testable.

### 7.5 Sensitive-topic gate

If matched topics include public safety, healthcare, legal, emergency, or elections (`SKILLS.md` §10), set `sensitive = true` and force `record_status = needs_review`, regardless of relevance score.

---

## 8. Deduplication specification

Signals, evaluated in order (`SKILLS.md` §8):

1. **Canonical URL** exact match.
2. **Normalized original URL** (strip tracking params, trailing slash, scheme/case).
3. **Content hash** — SHA-256 over normalized title + content_text + published date (normalization defined in §9).
4. **Near-duplicate title** + publication within a configured window (default 48h).
5. **Text fingerprint** (shingle/MinHash) — Phase 5, optional.

**Updates:** keep `first_seen_at`, `last_seen_at`, `updated_at`; write a row to `article_revisions` on meaningful content change; a layout-only change is not a new article.

**Cross-source duplicates are linked, not deleted.** If two different publishers independently publish the same press release (or one republishes another's story), **both source records are preserved** and a relationship is recorded in `article_relations` (`relation_type` = duplicate, syndicated, or update_of). `content_hash` is a deduplication *signal*, never a global uniqueness rule — deleting one publisher's record because its hash matches another's would destroy provenance.

**Never** merge two stories solely because they share keywords or the same incident.

---

## 9. Extraction and normalization rules

From `SKILLS.md` §7:

- Preserve original text, language, and script (Hindi Unicode intact).
- Normalize whitespace and HTML artifacts without changing meaning.
- Never silently correct spelling, names, numbers, dates, quotes, or places.
- Parse dates timezone-aware; store UTC internally; retain original timestamp/timezone.
- Date-only sources → set `published_date` and `published_time_precision = date-only`; leave `published_at` null. Never invent a time.
- Always preserve the publisher's original date/time string in `published_at_original`, even when a normalized value is derived.
- Reject error pages, access-denied pages, search pages, category indexes, unrelated content.
- Exclude comments/user replies.
- Strip nav, menus, ads, cookie banners, related-story widgets, boilerplate.
- Do not collect personal phone numbers, addresses, ID documents, or sensitive personal data.

**Normalization used for hashing** (must be deterministic and versioned): Unicode NFC, collapse whitespace, lowercase for comparison only, remove boilerplate, strip tracking parameters from URLs.

### 9.1 Extraction layering (fill-missing)

Extraction is layered per `ARCHITECTURE.md` §13: structured metadata (JSON-LD/Open Graph/canonical) → semantic DOM → registered source handler → trafilatura fallback. Later layers **fill missing fields only**; they never overwrite stronger or contradictory data. The confidence gate applies to the final `ArticleDraft`. Every parser returns the single canonical `ArticleDraft` contract (`ARCHITECTURE.md` §7.3); no field is ever guessed.

### 9.2 Language detection (script-based, deterministic)

Predominantly Devanagari → `hi`; predominantly Latin → `en`; genuinely mixed or weak evidence → `und`. Language is never forced when evidence is weak, and the original text is always preserved regardless of classification.

### 9.3 Raw response retention (metadata only)

Production stores response metadata only (status, final URL, fetched-at, duration, selected headers, cache info, error category). **Full raw HTML is never persisted** to the production database (`ARCHITECTURE.md` D13). Parser tests use deliberately saved, sanitized fixtures.

---

## 10. Source health tracking

Per source, per run (`SKILLS.md` §12):

`source_id`, `last_checked_at`, `last_success_at`, `last_http_status`, `last_error`, `consecutive_failures`, `average_fetch_duration`, `items_found_last_run`, `items_saved_last_run`, `last_article_published_at`, `source_activity_status`, `source_verification_status`.

**Rule:** one failed request never marks a source inactive. Distinguish active-and-collecting, active-but-no-new-items, temporarily unavailable, blocked, parser broken, inactive (repeated dated evidence), and pending verification.

---

## 11. Database schema (logical)

**v1 database: SQLite.** It suits the initial single-user, manually executed ingestion workflow: no server process, no administration, trivial backup (one file), and adequate write concurrency for sequential CLI runs.

**Future option: PostgreSQL**, adopted only if a demonstrated requirement appears (multi-user access, concurrent writers, or volume beyond SQLite's comfort). No PostgreSQL server, Docker, or DB administration is introduced in v1.

The logical schema and persistence layer are designed to be **portable, not identical**. See §11.1 for the concrete portability considerations.

| Table | Purpose | Key columns |
|---|---|---|
| `sources` | Canonical registry | source_id (PK), all §5 fields |
| `source_verifications` | Verification evidence history | id, source_id (FK), checked_at, method, http_status, robots_allowed, feed_found, sitemap_found, listing_found, parser_name, evidence, result, reviewer, **requests_consumed, request_budget, browser_used, verification_policy_version** (`ARCHITECTURE.md` §4.4) |
| `source_aliases` | Entity resolution | alias, source_id (FK) |
| `articles` | Clean records | id, source_id (FK), original_url, canonical_url, title, **published_at** (nullable UTC timestamp), **published_date** (nullable date), **published_time_precision** (date-only / timestamp / unknown), **published_at_original** (verbatim source string), updated_at, retrieved_at, content_text, author, language, content_type, location_mentions, bilaspur_relevance, relevance_reason, matched_terms, sensitive, extraction_method, content_hash, record_status, source_verification_status, first_seen_at, last_seen_at |
| `article_revisions` | Update history | id, article_id (FK), content_hash, changed_at, diff_summary |
| `article_urls` | All seen URLs per article | article_id (FK), url, url_kind |
| `article_relations` | Cross-source duplicate / syndication links | id, article_id (FK), related_article_id (FK), relation_type (duplicate / syndicated / update_of), confidence, detected_at |
| `discovery_backlog` | Additional Monitoring Targets | category, target, source_type, signals, preferred_locations, priority, registry_status, notes |
| `keyword_groups` | Watchlist | group_name, terms, coverage_purpose, usage_notes |
| `source_health` | Per-run metrics | source_id (FK), run_id (FK), all §10 fields |
| `ingestion_runs` | Run ledger (live runs only) | run_id, started_at, finished_at, mode (live), totals. **Dry-runs write no row** (`ARCHITECTURE.md` D5) |
| `verification_queue` | Read-only mirror | priority, task, why, evidence, state, action |

**Constraints:** unique on `(source_id, canonical_url)` only. `content_hash` is **indexed but never globally unique** — distinct publishers may legitimately publish the same press release; link them via `article_relations` and keep both records. FK enforcement on (`PRAGMA foreign_keys = ON` in SQLite). Transactions wrap multi-row writes.

### 11.1 SQLite v1 and PostgreSQL portability

SQLite and PostgreSQL are **not interchangeable**. The schema avoids unnecessary SQLite-specific coupling so a later migration is a controlled task, not a rewrite.

| Concern | SQLite (v1) | PostgreSQL (future) | Plan rule |
|---|---|---|---|
| Data types | Dynamic affinity; booleans as 0/1; dates often TEXT | Strict types; `BOOLEAN`, `TIMESTAMPTZ`, `DATE` | Store timestamps as UTC ISO-8601; map booleans explicitly in the persistence layer |
| Enums | TEXT + `CHECK` constraint | Native `ENUM` or lookup table | Keep one canonical enum definition in the application model |
| Primary keys | `INTEGER PRIMARY KEY` (rowid alias) | `BIGINT GENERATED … AS IDENTITY` | Use explicit surrogate keys; never rely on SQLite rowid behaviour |
| Upsert | `INSERT … ON CONFLICT` (modern SQLite) | `INSERT … ON CONFLICT` (different syntax details) | Isolate upsert SQL in the persistence layer |
| Full-text search | FTS5 | `tsvector` / GIN | Not required in v1; avoid FTS-specific schema now |
| Transactions | Single writer; use WAL mode | MVCC, high concurrency | Fine for manual CLI; revisit for concurrent writers |
| Concurrency | Writers serialize | Many concurrent writers | v1 is single-user, sequential — acceptable |
| Migrations | SQL files | SQL files / Alembic | Plain SQL migrations; no SQLite-only DDL |

**Configuration:** a single `DATABASE_URL` is read once by a configuration module and passed to the persistence layer. Database paths must **not** be hardcoded across application modules.

---

## 12. Configuration and secrets

Configuration is loaded from environment variables only and is never hardcoded in source files. The actual contact address is stored **only** in the local, untracked `.env`; it must not appear in this plan or any other tracked file. `.env.example` contains placeholders only, so the repository stays safe to publish.

Expected local `.env` shape (the address itself is deliberately not written here):

```env
USER_AGENT=BilaspurNewsBot/0.1 (+mailto:CONTACT_EMAIL)
CONTACT_EMAIL=<configured locally in .env>
```

- The contact email is configured locally in `.env`, not in tracked documentation.
- `.env` must remain **untracked** (listed in `.gitignore`).
- `.env.example` contains **placeholders only**.
- The project owner must confirm the email is actively monitored before the first live crawl.
- `USER_AGENT` identifies the project and includes that contact.
- If required contact configuration is missing, the CLI must **report it clearly and refuse to start a live crawl** (dry-run may proceed).

| Variable | Required | Default | Notes |
|---|---|---|---|
| `USER_AGENT` | **yes (live)** | none | Format `BilaspurNewsBot/0.1 (+mailto:CONTACT_EMAIL)`; real value in local `.env` |
| `CONTACT_EMAIL` | **yes (live)** | none | Configured locally in `.env`; never stored in tracked files |
| `DATABASE_URL` | yes | `sqlite:///data/news.db` | Single source of DB configuration |
| `REQUEST_TIMEOUT_S` | no | 20 | DNS/connect/read |
| `MAX_CONCURRENCY` | no | 4 | Global |
| `PER_DOMAIN_DELAY_S` | no | 2 | Politeness |
| `MAX_RETRIES` | no | 3 | Transient only; configured as Crawlee policy (single retry authority) |
| `BACKOFF_BASE_S` | no | 1 | Exponential + jitter |
| `VERIFICATION_REQUEST_BUDGET` | no | 10 | Hard cap per verification attempt (`ARCHITECTURE.md` D9) |
| `BROWSER_CRAWLING_ENABLED` | no | true | **Global kill-switch only** — may disable browsers everywhere; never grants permission (D10). Per-source approval still required |
| `RESPECT_ROBOTS` | no | true | Never disable in production |
| `DRY_RUN` | no | false | |
| `LOG_LEVEL` | no | INFO | Structured logs |

---

## 13. Testing strategy

### Unit (fixtures, no network)
- Registry loader: URL classification (real / section / search-stub / aggregator); alias resolution; **lifecycle transitions — a `candidate` is never auto-enabled**; `rejected → discovered` reopening requires an operator and an audit reason.
- Parsers: saved sanitized HTML and RSS fixtures per source family; headline, body, date, author, canonical, language; **fill-missing layering — a weaker layer never overwrites stronger or contradictory data**; every parser output validates as `ArticleDraft`.
- Relevance: table-driven cases — city, locality, nearby, ambiguous, Hindi-only, sensitive.
- Dedup: URL normalization, content hash stability, near-duplicate window, **and cross-source duplicates — same press release from two publishers keeps both records plus an `article_relations` link**.
- Normalizer: Hindi Unicode, whitespace, date-only vs timestamp, **`published_time_precision` and `published_at_original` preserved**; **script-based language detection (`hi`/`en`/`und`)**.
- Boilerplate removal.

### Integration (approved live set — three-source pilot)
- Only the three pilot candidates: District Administration (`bilaspur.gov.in`), Bilaspur Police (`bilaspur.cgpolice.gov.in`), SECL (`secl-cil.in`).
- A source is exercised only after it passes the §4.6 verification gate; a failed source is skipped, recorded, and left `disabled`.
- Assert real records validate against the Pydantic model.

### Failure injection
- 403, 429 (with Retry-After), timeout, redirect chain, malformed HTML/feed, changed layout, empty feed, **robots retrieval failure (`E_ROBOTS_UNAVAILABLE`)**, **verification budget exhaustion → `disabled`, never `rejected`**.
- **Single retry authority:** a retried request is retried by Crawlee only — never once by Crawlee and again by application code.

### Operational
- **Dry-run performs zero database writes of any kind** — no run row, articles, revisions, relations, or health; results appear in logs only.
- Dry-run selects `enabled` sources only.
- Two consecutive live runs create zero duplicates (idempotency).
- Interrupted live run is detected and marked `interrupted` on startup; re-running is safe.
- Circuit breaker opens after repeated failures and does not block other sources.
- Normal run with a healthy feed issues **no listing-page requests** (feed-only, D11).

---

## 14. Phased build order

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Registry import: Excel → SQLite `sources` for all **36 media publishers + 12 institution candidates**; aliases; keyword groups; discovery backlog; lifecycle (8 states) and verification fields; SQLAlchemy repositories; CLI `verify-source` | Every row classified; **zero sources enabled**; no live crawl performed |
| 1 | Fetcher + source-health + verification-gate tooling; **first pilot source (District Administration, Bilaspur)** taken through the full sequence: `verify-source` (hard 10-request budget) → operator approval → `run --source <id> --dry-run` (logs only, zero writes) → `run --source <id>` (live) | §4.6 criteria met; evidence row records `requests_consumed`/`request_budget`/`browser_used`/`verification_policy_version`; record validates; health written; never enabled without approval |
| 2 | **Three-source pilot** (District Administration, Bilaspur Police, SECL): verify and enable each **independently** | Each source passes all §4.6 criteria; failed sources documented and left `disabled`; budget exhaustion → `disabled`, never `rejected`; parser tests exist for every enabled source |
| 3 | Remaining **nine institutional candidates** verified individually; **local publisher candidates (11)** RSS-first | Only verified sources enabled; no unverified publisher crawled |
| 4 | Regional sections (6) + section discovery (4) | Sections crawl within permitted paths; homepages untouched |
| 5 | Relevance + dedup hardening | Rule tests green; cross-source relations proven; idempotency proven |
| 6 | Manual-run reliability hardening: backfill, repeat-run idempotency, health report | Consecutive manual runs produce correct records and **zero duplicates** |
| 7 | (Optional) Scheduling P1–P4 | Only after Phase 6 is proven; separate approval |
| 8 | (Deferred) social platforms | Separate approval required |

**Dependencies:** each phase depends on the previous one; Phase 2 does **not** require all 12 institutions to pass — only the three pilot sources. The other nine remain `candidate`/`disabled` until Phase 3.

**Status (2026-11-12):** Phases 0–2 complete; Phase 3 partially done.
- Phase 1 ✅ — district-administration-bilaspur went through the full sequence (verified via listing at 10/10 budget → operator approval → dry-run zero writes → live run); evidence row records `requests_consumed`/`request_budget`/`browser_used`/`verification_policy_version`; health written.
- Phase 2 ✅ — three-source pilot executed: district-administration-bilaspur verified + enabled + collected; bilaspur-police and SECL blocked by robots 403 → documented and left `disabled`, never rejected.
- Phase 3 (partial) — Wave-1 publishers verified via RSS (bilaspur-live, the-bilasa-times, takhatpur-ki-bhadas, raja-sahab-news-agency) and enabled + collected live (20 articles total, 6 healthy runs). Institutional candidates: municipal corporation, CMO, GGV, ABVV `rejected` (no collectable content at registered URLs); DEO + district court `disabled` (weak listing); zila-panchayat deliberately skipped (placeholder URL). P3 regional publishers remain `candidate`.
- Phase 1/2 pipeline extension completed: D11 fall-through (feed → sitemap → listing) implemented and tested; zero-request listing completion after verification budget exhaustion (approved amendment, recorded in ARCHITECTURE.md D9); relevance source-coverage corroboration (Bilaspur-focused sources resolve the ambiguous place name).

**Status (2026-11-15):** browser adapter (D10) implemented; five defects found in an audit and fixed; 129 tests pass.
- Browser adapter — `BrowserFetcher` (Chromium) returns the same `FetchResult` contract as the HTTP transport, so discovery, extraction, relevance, dedup and storage are unchanged. Feeds and sitemaps stay on HTTP; only HTML pages go through the browser. Per-source approval (`sources.browser_approved`) plus an operator name in the audit trail; the global `BROWSER_CRAWLING_ENABLED` switch may only *disable* browser crawling (D10). The JS-rendered localhost proof case (`scripts/js_demo_site.py` + `scripts/demo_browser_run.py`) runs all five phases end to end.
- Browser verification of the six HTTP-failure sites: GGV, district court and CMO → `rejected` (rendered page carries no article links); ABVV, municipal corporation, DEO → `disabled` (feed paths time out, listing weak); bilaspur-police and SECL → robots.txt 403 (WAF) — the browser renders the police homepage, but that site's notices are PDF-only, so the browser does not unlock it.
- **Five defects fixed (2026-11-15):**
  1. Run and health timestamps mixed UTC (`utcnow`) with naive local `datetime.now()`, skewing every recorded run by the local offset (5.5 h). All timestamps now UTC; the six historical skewed rows were migrated.
  2. The dry-run used the hardcoded default keyword groups while the live path loaded the registry's, so a dry-run could approve articles the live run then discarded. Both paths now use the registry groups, and the dry-run counts filtered items the same way the live path does (D5: a dry-run is a faithful preview).
  3. `BROWSER_CRAWLING_ENABLED` was read nowhere. It is now a real kill-switch: when off, an approved source falls back to HTTP.
  4. Re-importing the workbook reset `source_verification_status` on sources live verification had promoted. Import now keeps the stronger of the two and never downgrades.
  5. A source interrupted mid-verification was stuck in `verification_pending`: the CLI refused it and the state machine had no exit. `verification_pending → discovered` is now a legal recovery transition, and `verify-source` accepts a pending source.
- Live sweep after the fixes (5 enabled sources): 4 collected successfully (bilaspur-live, takhatpur-ki-bhadas, the-bilasa-times, raja-sahab-news-agency — 27 articles, 27 revisions, 12 runs). `district-administration-bilaspur` failed honestly with `E_ROBOTS_UNAVAILABLE` (robots.txt timed out on bilaspur.gov.in). `raja-sahab-news-agency` served HTTP 200 with empty bodies (verified site-side with curl and Chromium — the site is serving blank HTML; its RSS feed still works).
- **Version control:** git initialised on branch `main` with the first commit (`feat: Bilaspur News Scraper v1`). The local `.env` and `data/*.db` are excluded by `.gitignore`; the contact email appears only in the untracked `.env`, and `.env.example` carries placeholders. **Published to `https://github.com/wrobr27-debug/bilaspur-news-scraper` (public, branch `main`, 2 commits, 67 files).** Verified by a fresh anonymous clone: 129 tests pass, and neither `.env` nor the database is present.
- Regression coverage: `tests/unit/test_bug_fixes.py` (12 tests) locks down each of the five fixes; every one of them was mutation-checked — reintroducing the defect makes the matching test fail.

---

## 15. Risks and open questions

### Risks
1. **14 of 36 publishers are Google-search stubs** — not crawlable. Expect lower fleet size than the row count suggests.
2. **Publisher quality variance** — some local sites may be inactive, low-quality, or unverifiable; several are explicitly "candidate" or "unverified" in the registry.
3. **Regional homepages lack a Bilaspur path** — crawling them violates §5.3 path restriction until a section is found.
4. **Ambiguous "Bilaspur"** — mitigated by the Chhattisgarh co-occurrence guard **plus source-coverage corroboration (implemented 2026-11-12)**: sources whose declared scope is Bilaspur city/district resolve the ambiguity; Hindi/English mixed text still classifies as `und` when script evidence is weak.
5. **Copyright/images** — store URLs + attribution metadata only; no automatic download (§4.2, §16).
6. **Terms of service and robots** — enforced by the fetcher; blocked sources are logged, not bypassed.
7. **Language detection** — resolved as script-based detection (`hi`/`en`/`und`, `ARCHITECTURE.md` D12); Hindi/English code-mixing will still need tuning and will classify as `und` when evidence is weak.
8. ~~**No source is verified yet**~~ **(resolved 2026-11-12)** — 5 sources verified with evidence, 1 enabled + collected live; blocked sources documented and left `disabled`.
9. **SQLite write concurrency** — adequate for manual, sequential runs but not concurrent writers (§11.1).

### Open questions for the owner
1. Contact email is configured locally in `.env` (§12); owner to confirm it is actively monitored before the first live crawl. **(First live crawl happened 2026-11-12 — confirm monitoring.)**
2. Who holds approval authority for the `verified → enabled` transition? **(answered 2026-11-12)** — the owner delegates via chat; approvals are recorded as `owner (chat approval 2026-11-12)` in the audit trail.
3. Should the **14 search-stub publishers** get a manual URL-resolution pass before Phase 3, or stay parked?

*Resolved during architecture review:* the three-source pilot is approved (§2, §4.6); the verification budget (10), browser approval model, feed-only policy, dry-run semantics, language detection, raw-response retention, and parser order are decided in `ARCHITECTURE.md` §20; PostgreSQL is future-only (§11.1); WordPress is out of scope for v1 (§16).

---

## 16. Out of scope for v1

- Social platforms (YouTube, Facebook, Instagram, X) — schema-ready, disabled.
- Public figures as sources.
- Crawling the 61 Additional Monitoring Targets.
- Executing the Verification Queue.
- AI summarization, translation, or article writing.
- WordPress or social publishing.
- Downloading or reusing copyrighted images.
- Bypassing any access control, login wall, paywall, CAPTCHA, or rate limit.
- PostgreSQL server setup, Docker, or database administration in v1 (§11.1).
- Automatically resolving Google-search stubs into sources, or following search results to create enabled sources.
- Scheduling/automatic cadence in v1 (optional Phase 7 only).

---

## 17. Definition of done (per source)

A source is "done" only when (`SKILLS.md` §15):

- its URL is canonical and belongs to the intended entity;
- verification and activity states are recorded accurately, with evidence and timestamp, including `requests_consumed`, `request_budget`, `browser_used`, and `verification_policy_version` (§4.5);
- the source is set to `enabled` only after passing the §4.6 verification gate;
- a permitted collection method is documented;
- items are discoverable;
- fields extract accurately across multiple recent items;
- publication date precision and the original source date string are preserved;
- relevance is evidence-based and stored with reason;
- duplicates and updates are handled, including cross-source syndication links without deleting either record;
- rate limits and failures are handled safely;
- tests pass and parser version is recorded;
- source health metrics are written;
- nothing is published anywhere automatically.

---

## 18. Appendix — workbook-to-system mapping

| Workbook sheet | System destination | Active in v1 |
|---|---|---|
| README | Documentation | — |
| Media Publishers | `sources` (36 classified) | Conditional — only verified, non-excluded sources enabled |
| Platform Accounts | `sources` (disabled) | No |
| Public Figures | `sources` (disabled) | No |
| Institutions & Business | `sources` (12 candidates; verified sources only enabled) | Conditional |
| Verification Queue | `verification_queue` (read-only) | No (human) |
| Research References | Documentation only | No |
| Additional Monitoring Targets | `discovery_backlog` | No |
| Keyword Watchlist | `keyword_groups` → relevance engine | Yes |
| Monitoring Priorities | `sources.priority` (labels only) | Yes — priority labels imported; scheduling deferred (Phase 7) |

**"Conditional"** means the rows are imported into the registry, but no source is active until it passes the §4.6 verification gate and is approved. No row in this table implies that a source is currently verified or crawlable.

---

*End of plan. This document references `ARCHITECTURE.md` as the detailed design of record. Next step after joint review and approval of both documents: Phase 0 — build the registry importer and source classifier.*
