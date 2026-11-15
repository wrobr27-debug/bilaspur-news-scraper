# ARCHITECTURE.md — Bilaspur News Scraper v1

**Status:** Architecture approved with clarifications — incorporated. **Not implemented.**
**Date:** 2026-11-11
**Companion to:** `PLAN.md` (implementation roadmap)
**Governing spec:** `SKILLS.md`
**Scope:** news collection and storage only — no dashboard, AI summarization, social integrations, publishing, or scheduler.

> This document is the detailed design of record. `PLAN.md` references its contracts, lifecycle, component boundaries and operational rules rather than duplicating them. It contains no application code. Implementation begins only after both documents are approved.

---

## 0. Decisions (approved)

| ID | Decision | Choice |
|---|---|---|
| D1 | Application shape | **Modular monolith** — one Python app, one SQLite database, explicit module contracts. Not microservices. |
| D2 | Crawling strategy | **HTTP-first** (`HttpCrawler`), Playwright fallback only when JS rendering is demonstrably required. |
| D3 | System split | **Control Plane** (registry, verification, configuration, runs) vs **Collection Pipeline** (discover → fetch → extract → process → store). |
| D4 | Verification vs ingestion | **Separate workflows** with separate request profiles and budgets. |
| D5 | Dry-run semantics | **Logs only — zero database writes of any kind.** Dry-run never grants access to unverified sources. |
| D6 | Extraction contract | Every parser returns one canonical **`ArticleDraft`**. |
| D7 | Persistence | SQLAlchemy 2.x + repository layer over SQLite; no SQLite-specific coupling in domain modules. |
| D8 | Crawlee | Primary crawling framework and the **single retry authority** for HTTP fetches. |
| D9 | Verification budget | **Hard cap of 10 outbound requests per attempt**, one host, no recursion/pagination. Counts redirects and retries. Budget exhaustion → `disabled`/pending, **never** `rejected`. |
| D10 | Browser approval | **Per-source explicit approval** (`requires_js` + evidence + operator sign-off). A global switch may only *disable* browsers, never grant permission. Browser concurrency = 1. |
| D11 | Feed policy | **Feed-only during normal runs** when a feed is available. Bounded listing cross-check only during verification or a manual diagnostic. If the feed is unavailable, fall through to sitemap → listing. |
| D12 | Language detection | **Script-based, deterministic**: predominantly Devanagari → `hi`, predominantly Latin → `en`, mixed/weak evidence → `und`. Never force a language. |
| D13 | Raw responses | **Metadata only in production** (status, final URL, timings, selected headers, cache info, error category). No full raw HTML persisted; sanitized fixtures are used for tests. |
| D14 | Parser layering | Layered **fill-missing** strategy: structured → semantic DOM → registered handler → trafilatura. Later layers fill gaps; they never overwrite stronger or contradictory data. Confidence gate applies to the final draft. |
| D15 | Rejected recovery | `rejected` is **not terminal**. An operator may return it to `discovered` with an audit reason when new evidence justifies re-verification. |
| D16 | Error categories | **One canonical category per failure** (see §10). `429` → `E_RATE_LIMITED` only; explicit robots disallow → `E_POLICY`; robots retrieval failure → `E_ROBOTS_UNAVAILABLE`. |
| D17 | Verification evidence | Evidence records **requests consumed, budget, browser usage, and verification policy version** alongside the result. |
| D18 | `httpx` boundary | Permitted only for narrow control-plane checks; never a second crawler. Crawlee remains the crawling authority. |

---

## 1. Scope and boundaries

### In scope (v1)

1. Know which websites it may collect from.
2. Visit them politely.
3. Discover articles/notices.
4. Extract original content and metadata.
5. Classify Bilaspur relevance.
6. Detect duplicates and updates.
7. Validate records.
8. Store structured records.
9. Record what happened each run.

### Out of scope (v1)

Dashboard, AI summarization, translation, social platforms, publishing, scheduling, image downloading, WordPress.

### Hard invariants

- No article is ever published automatically.
- No source is crawled unless `enabled`.
- Verification requests are not collection.
- Original source identity and URL are never lost.
- Hindi/English original text is preserved.
- A scraped item is a lead, not a verified fact.

---

## 2. System context

```
┌──────────────────────────── CONTROL PLANE ────────────────────────────┐
│  Configuration   Source Registry   Verification Manager   Run Manager  │
│                                                                        │
│  Only enabled sources enter normal collection                          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ eligible sources
┌───────────────────────────────▼──────────────────────────────────────┐
│                       COLLECTION PIPELINE                             │
│                                                                        │
│  Selector → Discovery → Fetch → Extract → Normalize → Relevance        │
│           → Deduplicate → Validate → Persist                          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                        ┌───────▼────────┐
                        │  SQLite (v1)   │
                        │ sources,       │
                        │ verifications, │
                        │ articles,      │
                        │ revisions,     │
                        │ relations,     │
                        │ runs, health   │
                        └───────┬────────┘
                                │
                     Human editorial review
```

**Rule:** the Collection Pipeline cannot be invoked for a source that is not `enabled`. The only workflow that may touch a non-enabled source is the Verification Manager, under its own budget.

---

## 3. Two-plane responsibility model

### Control Plane

| Component | Responsibility | Writes |
|---|---|---|
| Configuration | Load/validate env config | none |
| Source Registry | Import workbook, aliases, keyword groups, backlog; lifecycle state | `sources`, `source_aliases`, `keyword_groups`, `discovery_backlog` |
| Verification Manager | Controlled checks, evidence, lifecycle transitions | `source_verifications`, `sources` lifecycle fields |
| Run Manager | CLI orchestration, run context, recovery | `ingestion_runs` |

### Collection Pipeline

| Stage | Responsibility |
|---|---|
| Source Selector | Choose eligible `enabled` sources for this run |
| Discovery Engine | Produce candidate article URLs (RSS/Atom → sitemap → listing) |
| Fetch Manager | Polite HTTP (or approved browser) fetch with cache + limits |
| Extraction Manager | Convert fetched page/feed into `ArticleDraft` |
| Normalizer | Deterministic text/date/URL normalization |
| Relevance Engine | Rule-based Bilaspur relevance + sensitive flag |
| Deduplication Engine | New / duplicate / update / cross-source relation |
| Validation Engine | Pydantic contract + data-quality gates |
| Persistence Engine | Transactional writes |
| Monitoring | Run ledger, source health, structured logs |

---

## 4. Source lifecycle state machine

### 4.1 States

| State | Meaning | Eligible for collection? |
|---|---|---|
| `candidate` | Imported from workbook; not investigated | No |
| `discovered` | A genuine source URL has been identified | No |
| `verification_pending` | Verification authorized and in progress | No (verification requests only) |
| `verified` | Passed verification; evidence recorded | No (awaiting enablement) |
| `enabled` | Verified **and** approved | **Yes** |
| `disabled` | Previously enabled, paused | No |
| `rejected` | Verification failed; recoverable only by operator-authorized re-verification | No |
| `excluded` | Not a source (search stub, aggregator, non-collectable) | No |

### 4.2 Allowed transitions

| From | To | Guard | Actor |
|---|---|---|---|
| `candidate` | `discovered` | Genuine URL recorded; not a search stub/aggregator | Registry / operator |
| `candidate` | `excluded` | Classified as search stub, aggregator, or not a source | Registry |
| `discovered` | `verification_pending` | Verification request authorized | Operator via CLI |
| `verification_pending` | `verified` | All verification criteria pass; evidence + timestamp written | Verification Manager |
| `verification_pending` | `rejected` | Permanent failure (identity mismatch, robots disallow, no collectable content) | Verification Manager |
| `verification_pending` | `disabled` | Temporary/blocked failure **or budget exhausted**; may retry later | Verification Manager |
| `verified` | `enabled` | Explicit approval recorded (`verified_by`) | Operator |
| `verified` | `verification_pending` | Re-verification requested (site changed) | Operator |
| `enabled` | `disabled` | Operational failure, circuit break, or owner decision | Monitoring / Operator |
| `disabled` | `verification_pending` | Re-verification before reactivation | Operator |
| `disabled` | `enabled` | Only if still `verified` and cause was transient; approval recorded | Operator |
| `rejected` | `discovered` | Operator-authorized reopening after a material change; **audit reason recorded** | Operator |
| `verified`/`enabled` | `excluded` | Reclassified as not a source | Operator |

### 4.3 Forbidden transitions (enforced)

- `candidate → enabled` (skips verification)
- `candidate → verification_pending` without a genuine URL (`discovered` required)
- `discovered → enabled`
- `verification_pending → enabled` (must pass through `verified`)
- any → `enabled` without `verified_by` and evidence
- `excluded → enabled` without a new `discovered` record
- `rejected → enabled` without returning to `discovered` and re-verifying
- `rejected` is **not terminal**: reopening requires an operator, new evidence, an audit reason, and a `rejected → discovered` transition before re-verification.

### 4.4 Evidence record

Every `verified`/`rejected` transition writes a `source_verifications` row:

`source_id`, `checked_at`, `method` (rss/sitemap/listing/manual), `http_status`, `robots_allowed`, `feed_found`, `sitemap_found`, `listing_found`, `parser_name`, `evidence`, `result`, `reviewer`, **`requests_consumed`**, **`request_budget`** (10), **`browser_used`**, **`verification_policy_version`**, `blocking_reason`.

Verification state is **never** inferred from a URL's presence in the workbook. The budget fields make the 10-request limit auditable.

---

## 5. Verification workflow vs ingestion workflow

### 5.1 Verification workflow (Control Plane)

**Purpose:** decide whether a candidate is suitable. It is *not* collection.

- **Entry:** operator runs `verify-source <source_id>`.
- **Permitted targets:** one host only; the candidate URL, `robots.txt`, a small set of feed/sitemap candidates, and one listing page.
- **Request budget:** a **hard cap of 10 outbound requests per attempt**, counted per attempt — including redirects and retries. One host, no recursion, no pagination crawl.
- **Budget exhaustion:** if the budget runs out before a conclusion, leave the source `disabled`/pending. **Never mark it `rejected` merely because the budget was exhausted.**
- **Zero-request completion (approved amendment):** after budget exhaustion the engine may still finish analysis that requires **no outbound request** — specifically the listing check on the homepage already fetched within budget. If the homepage carries >= 3 article links the attempt concludes `verified` (method `listing`); otherwise it concludes `disabled` as above. The 10-request cap is never exceeded, and the evidence records that the conclusion used zero additional requests.
- **No article persistence.** Writes only `source_verifications` and lifecycle fields.
- **Browser:** not used by default; permitted only with per-source explicit approval (D10).
- **Outcome:** `verified` / `rejected` / `disabled` (temporary or budget-exhausted) with recorded evidence, including `requests_consumed`, `request_budget`, `browser_used`, and `verification_policy_version`.

### 5.2 Normal collection (Collection Pipeline)

- **Eligible sources:** `enabled` only.
- **Workflow:** selector → discovery → fetch → extract → normalize → relevance → dedup → validate → persist.
- **Writes:** `articles`, `article_urls`, `article_revisions`, `article_relations`, `source_health`, `ingestion_runs`.

### 5.3 Dry-run semantics (the correction)

| Mode | Sources allowed | Writes |
|---|---|---|
| `verify-source` | `candidate` / `discovered` / `disabled` / `rejected` (reopen) | Verification evidence + lifecycle only |
| `run --dry-run` | **`enabled` only** | **Zero database writes of any kind** — no run row, articles, revisions, relations, or health |
| `run` (live) | `enabled` only | Full transactional writes |

**Dry-run logging:** because a dry run writes nothing, it emits structured logs containing the run ID, selected sources, discovered items, extraction results, validation failures, and timings. Live runs additionally persist the `ingestion_runs` record.

**Dry-run does not mean "access to unverified sites."** Evaluating an unverified candidate is a *verification* action with a verification budget, not a dry-run of normal collection.

### 5.4 Pilot reconciliation

The pilot (District Administration, Bilaspur Police, SECL) uses:

1. `verify-source` for each (hard 10-request budget) → `verification_pending → verified/rejected/disabled`. Budget exhaustion → `disabled`, never `rejected`.
2. If `verified`, operator approves → `enabled`.
3. `run --source <id> --dry-run` — **logs only, zero database writes** — to validate extraction on real pages.
4. Only then `run --source <id>` for live writes.

This resolves the current `PLAN.md` Phase 1 wording without granting unverified access.

---

## 6. Component specifications

Each component defines **inputs, outputs, invariants, and failure behaviour**. Domain modules never import the CLI or each other's internals; they communicate through typed contracts (§7) and ports.

### 6.1 Configuration

| Aspect | Specification |
|---|---|
| Input | Environment variables; optional local config |
| Output | Immutable `Settings` object |
| Invariants | Required contact config validated; DB URL resolved once; no hardcoded paths |
| Failure | Missing/invalid config → fail fast before any request (dry-run may proceed without contact) |

### 6.2 Source Registry

| Aspect | Specification |
|---|---|
| Input | Workbook path (Phase 0) or registry export |
| Output | `SourceRecord[]`, `SourceAlias[]`, `KeywordGroup[]`, `DiscoveryBacklogItem[]` |
| Invariants | Idempotent import; **never sets `enabled`**; preserves `origin_sheet`/`origin_row`; search stubs → `excluded` |
| Failure | Malformed rows quarantined with reason; import continues |

### 6.3 Verification Manager

| Aspect | Specification |
|---|---|
| Input | `source_id`, `VerificationPolicy` |
| Output | `VerificationReport` (incl. `requests_consumed`, `request_budget`, `browser_used`, `verification_policy_version`), lifecycle transition, evidence rows |
| Invariants | **Hard 10-request budget** counting redirects and retries; one host; no recursion/pagination; no article writes; browser only with per-source approval; budget exhaustion → `disabled`/pending, never `rejected` |
| Failure | Categorised failure recorded; source stays `disabled`/`rejected`; budget exhaustion → `disabled` |

### 6.4 Run Manager

| Aspect | Specification |
|---|---|
| Input | CLI args, `Settings` |
| Output | `RunContext` (`run_id`, mode, selected sources, budgets), `RunSummary` |
| Invariants | One writer; run opened at start, closed at end; interrupted runs detected on startup |
| Failure | Partial source failure does not abort the run |

### 6.5 Source Selector

| Aspect | Specification |
|---|---|
| Input | Registry, run mode, priorities |
| Output | Ordered `source_id[]` eligible this run |
| Invariants | Ingestion selects `enabled` only; excludes `disabled`/`excluded`/cooldown; deterministic order (priority, then `source_id`) |
| Failure | Empty selection → run completes with zero sources, not an error |

### 6.6 Discovery Engine

| Aspect | Specification |
|---|---|
| Input | `SourceRecord`, `DiscoveryPolicy` |
| Output | `DiscoveryItem[]` (`url`, `method`, `evidence`, `discovered_at`) |
| Invariants | Order: RSS/Atom → sitemap → listing; **feed-only on normal runs when a feed is available**; bounded listing cross-check only during verification or a manual diagnostic; bounded result count; URL canonicalization/filtering; no article body fetch |
| Failure | Each strategy failure recorded; fall through to sitemap → listing only when the feed is unavailable; all fail → source-level discovery failure |

### 6.7 Fetch Manager

| Aspect | Specification |
|---|---|
| Input | URLs + `FetchPolicy` (profile: verification/collection) |
| Output | `FetchResult` (status, `final_url`, selected headers, timing, `cache_hit`, `error_category`, `used_browser`). Raw body is **in-memory only and never persisted**. |
| Invariants | Robots gate; per-host limits; ETag/Last-Modified; **Crawlee is the single retry authority**; in-run URL dedup; browser only with per-source approval |
| Failure | Exactly one canonical error category (§10); never escalates aggressiveness |

### 6.8 Extraction Manager

| Aspect | Specification |
|---|---|
| Input | `FetchResult`, `SourceRecord` |
| Output | `ArticleDraft` or `ExtractionFailure` |
| Invariants | Layered **fill-missing** strategy (§13); later layers fill gaps and never overwrite stronger or contradictory data; handler registry keyed by `source_id`/domain; content confidence gate on the final draft; preserves original text |
| Failure | No confident extraction → failure category `E_PARSE`; no guessed fields |

### 6.9 Normalizer

| Aspect | Specification |
|---|---|
| Input | `ArticleDraft` |
| Output | normalized `ArticleDraft` |
| Invariants | Pure/deterministic; Unicode NFC; Hindi preserved; date precision preserved (`published_at`, `published_date`, `published_time_precision`, `published_at_original`) |
| Failure | Never throws away original values |

### 6.10 Relevance Engine

| Aspect | Specification |
|---|---|
| Input | normalized draft, keyword groups |
| Output | `bilaspur_relevance`, `relevance_reason`, `matched_terms`, `sensitive` |
| Invariants | Rule-based; Chhattisgarh co-occurrence guard; keyword alone is not proof |
| Failure | Insufficient text → `unknown` |

### 6.11 Deduplication Engine

| Aspect | Specification |
|---|---|
| Input | draft + read-only `DuplicateLookupPort` |
| Output | `DedupDecision` (`new`/`duplicate`/`update`/`related`) + matches |
| Invariants | Five signals; no global content-hash uniqueness; cross-source duplicates preserved and linked |
| Failure | Lookup unavailable → abort item, do not insert blind |

### 6.12 Validation Engine

| Aspect | Specification |
|---|---|
| Input | draft + dedup decision |
| Output | valid `ArticleRecord` or `ValidationFailure` |
| Invariants | Pydantic contract; enums; required provenance; no fabricated fields |
| Failure | Invalid → quarantine; never weaken the model |

### 6.13 Persistence Engine

| Aspect | Specification |
|---|---|
| Input | validated record, decisions, run context |
| Output | persisted ids, transaction result |
| Invariants | Per-article transaction; URL uniqueness per source; idempotent upsert; repositories hide SQL |
| Failure | Storage error rolls back that article only; run continues where safe |

### 6.14 Monitoring

| Aspect | Specification |
|---|---|
| Input | run + fetch events |
| Output | `source_health` rows, `RunSummary`, structured logs |
| Invariants | One source failure never marks it inactive; failure categories preserved |
| Failure | Monitoring failure must not corrupt article data |

### 6.15 CLI

Commands: `verify-source`, `run`, `run --source`, `run --dry-run`, `health`, `report` (later). Non-zero exit on fatal/config errors; per-source failures reported without aborting the run.

---

## 7. Canonical data contracts

### 7.1 `DiscoveryItem`

`source_id`, `url`, `method` (rss/sitemap/listing), `evidence`, `discovered_at`, `published_hint` (optional), `title_hint` (optional).

### 7.2 `FetchResult`

`url`, `final_url`, `http_status`, `headers` (subset), `raw_body` (in-memory only; **never persisted to the database**), `fetched_at`, `duration_ms`, `cache_hit`, `error_category` (nullable), `used_browser`.

### 7.3 `ArticleDraft` (extraction → processing)

`source_id`, `original_url`, `canonical_url`, `title`, `content_text`, `author`, `language`, `published_at`, `published_date`, `published_time_precision`, `published_at_original`, `content_type`, `location_mentions`, `extraction_method`, `parser_name`, `parser_version`, `featured_image_url`, `summary_from_source`, `tags`, `categories`.

### 7.4 `ArticleRecord` (validated → persistence)

`ArticleDraft` + `bilaspur_relevance`, `relevance_reason`, `matched_terms`, `sensitive`, `content_hash`, `record_status`, `source_verification_status`, `retrieved_at`, `first_seen_at`, `last_seen_at`.

### 7.5 `VerificationReport`

`source_id`, `checked_at`, `identity_ok`, `robots_allowed`, `http_status`, `feed_found`, `sitemap_found`, `listing_found`, `parser_name`, `evidence`, `result`, `blocking_reason`, `requests_consumed`, `request_budget`, `browser_used`, `verification_policy_version`.

### 7.6 `RunSummary`

`run_id`, `mode`, `started_at`, `finished_at`, `sources_selected`, `sources_succeeded`, `sources_failed`, `items_discovered`, `items_saved`, `items_quarantined`, `errors_by_category`.

### 7.7 Ports

- `DuplicateLookupPort` — read-only queries used by dedup (keeps processing pure).
- `Clock` — injectable time for determinism in tests.
- `FetcherPort` — allows verification and collection to share one fetch implementation.

---

## 8. Module dependency rules

```
schemas  ←  everything
config   ←  everything
database ←  schemas, config
registry ←  schemas, database
crawling ←  schemas, config
verification ← registry, crawling, database
discovery ← schemas, crawling
extraction ← schemas, crawling
processing ← schemas (via ports)
pipeline/run ← all of the above
cli ← pipeline + verification
```

**Rules:** no cycles; `processing` never imports `database` (uses ports); `extraction` never writes; `cli` is the only entry point; modules communicate through contracts, not shared mutable state.

---

## 9. Concurrency and resource model

- Global concurrency is a configured cap; per-host concurrency is lower (proposal: 1–2).
- Per-host delay honours `crawl-delay`; default 2s.
- Browser fallback concurrency is 1 and only for approved sources.
- SQLite has a single writer; persistence serializes writes and batches where safe. WAL mode enabled.
- One in-run URL set prevents duplicate fetches within a run.

---

## 10. Error taxonomy and retry matrix

| Category | Canonical condition | Retry? | Effect |
|---|---|---|---|
| `E_CONFIG` | missing/invalid configuration | No | Fatal; refuse to start |
| `E_POLICY` | explicit robots disallow; private/policy-restricted page | No | Blocked; quarantine/none |
| `E_ROBOTS_UNAVAILABLE` | `robots.txt` cannot be fetched or parsed | No (conservative) | Treat as disallowed; block |
| `E_DNS` | name-resolution failure | Yes (bounded) | Transient |
| `E_CONNECT` | TCP/connection failure | Yes (bounded) | Transient |
| `E_TLS` | certificate/handshake failure | No | Permanent for the source |
| `E_HTTP_CLIENT` | 400, 401, 403, 404, 410 | No | Blocked/permanent |
| `E_RATE_LIMITED` | **429 only** | Yes; honour `Retry-After` | Transient |
| `E_HTTP_SERVER` | 500, 502, 503, 504 | Yes (bounded, jitter) | Transient |
| `E_TIMEOUT` | connect/read/navigation timeout | Yes (bounded) | Transient |
| `E_BUDGET` | verification request budget exhausted | No | `disabled`/pending; **never `rejected`** |
| `E_PARSE` | malformed feed, layout change, no confident extraction | No | Quarantine; parser health |
| `E_VALIDATION` | contract/data-quality violation | No | Quarantine |
| `E_STORAGE` | database failure | One retry | Abort item/run |

**One category per failure.** `429` is only `E_RATE_LIMITED`; an explicit robots disallow is `E_POLICY`; a robots retrieval failure is `E_ROBOTS_UNAVAILABLE`. The classifier assigns exactly one category at the point of failure.

**Single retry authority:** Crawlee is the sole retry authority for HTTP fetches. Our policy is expressed as Crawlee configuration (max attempts, exponential backoff with jitter, `Retry-After` handling). Application code records outcomes and **never re-issues a fetch**. Non-fetch operations (e.g. database writes) may have their own small bounded retry. A site returning errors never triggers more aggressive scraping.

---

## 11. Transaction boundaries and run recovery

| Scope | Boundary |
|---|---|
| Article | One transaction: resolve dedup → insert/update article + urls + relations + revision |
| Source health | Per-source transaction, independent of articles |
| Run ledger | `ingestion_runs` opened at start, closed at end |
| Verification | One transaction per `verify-source` invocation (evidence + lifecycle) |

**Recovery:** on startup, runs with `finished_at IS NULL` are marked `interrupted`. Re-running is safe because ingestion is idempotent (`(source_id, canonical_url)` unique; content hash is a signal only). Per-article transactions prevent half-written records.

---

## 12. Discovery engine detail

**Order:** RSS/Atom → sitemap → listing page.

1. **Feed** — known feed URL, else `<link rel="alternate" type="application/rss+xml">`; parse `feedparser`; extract entry links, titles, dates, categories.
2. **Sitemap** — `Sitemap:` in `robots.txt`, else `/sitemap.xml`; follow index files within bounds; filter by date/path.
3. **Listing** — configured listing selectors; bounded pagination; extract article links.

Every item carries `method` + `evidence`. Strategies are bounded (max items, max pages, date window). Discovery never fetches article bodies; that belongs to Fetch Manager. **Normal runs are feed-only when a feed is available.** A bounded listing cross-check is permitted only during verification or a manual diagnostic when feed health suggests missing or stale content. If the feed is unavailable, fall through to sitemap, then listing.

---

## 13. Extraction / parser architecture detail

**Layered fill-missing strategy — not "first result wins":**

1. **Structured metadata** — JSON-LD (`NewsArticle`, `Article`), Open Graph, canonical link.
2. **Semantic DOM** — `<article>`, headings, `time[datetime]`, author markup.
3. **Registered handler** — per-source selectors (only when generic layers fail or are incomplete).
4. **Readability fallback** — `trafilatura` for main text.

Each layer contributes fields. A later layer fills a field only when it is missing or the earlier value is demonstrably weaker; it **never overwrites stronger or contradictory data** (e.g. structured metadata may supply a title and date but no body). The confidence gate is applied to the complete `ArticleDraft`, rejecting pages with too little body text, only error/boilerplate content, or contradictory metadata. Handler registry is keyed by `source_id`/host with `parser_name` + `parser_version`. **No guessing** of author, date, or location.

---

## 14. Crawlee usage

- **`HttpCrawler`** for feeds, sitemaps, listing pages, and article pages reachable over HTTP.
- **`PlaywrightCrawler`** only for sources flagged `requires_js` with evidence and **per-source operator approval**; browser concurrency 1. A global switch may only *disable* browsers, never grant permission.
- **Crawlee is the single retry authority.** Our error categories, attempt limits, backoff, and `Retry-After` rules are expressed as Crawlee configuration; the application never re-issues a fetch.
- `httpx` is permitted only for narrow control-plane checks (e.g. `robots.txt` probe), never as a parallel crawler.
- Wait conditions for browser: specific content selector or page-ready signal, not arbitrary sleeps.

---

## 15. Database responsibilities

| Area | Tables |
|---|---|
| Source management | `sources`, `source_aliases` |
| Verification | `source_verifications` |
| News storage | `articles`, `article_urls` |
| Article history | `article_revisions`, `article_relations` |
| Collection tracking | `ingestion_runs`, `source_health` |
| Configuration data | `keyword_groups` |
| Future discovery | `discovery_backlog` |

**Mandatory:** an article never loses its original source identity and URL. Five publishers of one press release = five preserved records, linked via `article_relations`.

---

## 16. Observability

- Structured logs with stable error codes; no credentials, cookies, tokens, or private data.
- Per-run summary and per-source health (§7.6, §10).
- Failure categories drive health states: active, active-but-no-new, temporarily unavailable, blocked, parser broken, inactive, pending verification.

---

## 17. Security and politeness

- Descriptive `USER_AGENT` with `CONTACT_EMAIL` loaded from local `.env` (never tracked).
- `robots.txt` respected; `crawl-delay` honoured.
- No login walls, paywalls, CAPTCHAs, or anti-bot bypass.
- No private groups/accounts.
- Public content only; no personal/sensitive data collection.
- No copyrighted image download.

---

## 18. Testing architecture

- **Unit** — registry/lifecycle transitions (including `rejected → discovered` with an audit reason), discovery filters (feed-only normal runs), parsers against fixtures, **fill-missing extraction**, normalizer, relevance rules, dedup, validation.
- **Contract** — every parser returns a valid `ArticleDraft`; every module respects its I/O contract.
- **Integration** — three pilot sources, only after verification; fixtures preferred over live where possible.
- **Failure scenarios** — 403/404/429/timeout/TLS/malformed feed/changed layout/robots disallow; **budget exhaustion → `disabled`, never `rejected`**; **single retry authority** (no double retry).
- **Operational** — dry-run performs **zero database writes of any kind**; two consecutive live runs produce zero duplicates; an interrupted run recovers safely.

---

## 19. Inconsistencies resolved in `PLAN.md`

These are now applied to `PLAN.md`:

1. **Phase 1 wording** — replaced with the four-step pilot sequence in §5.4 (verify → approve → dry-run → live).
2. **Lifecycle** — `verification_pending` and `rejected` added; strict transition table adopted (§4).
3. **Two planes** — Control Plane vs Collection Pipeline added to the pipeline/architecture sections.
4. **Dry-run** — defined as **logs only, zero database writes**, enabled sources only (§5.3).
5. **Extraction contract** — `ArticleDraft` is the single parser output (§7.3).
6. **Error taxonomy** — canonical category table and retry matrix adopted (§10).
7. **Transaction boundaries / recovery** — adopted (§11).
8. **SQLAlchemy + repositories** — added to the stack and persistence module.
9. **`httpx` boundary** — control-plane only, never a second crawler.
10. **Rejected recovery** — `rejected → discovered` with audit reason (§4.2).
11. **Verification evidence** — budget/browser/policy-version fields added (§4.4, §7.5).
12. **Retry ownership** — Crawlee is the single HTTP retry authority (§10, §14).
13. **Canonical error categories** — one category per failure (§10).

---

## 20. Approved decisions and clarifications

### 20.1 Seven decisions (final)

| # | Decision | Resolution |
|---|---|---|
| 1 | Verification budget | Hard **10** per attempt; counts redirects and retries; one host; no recursion/pagination; exhaustion → `disabled`/pending, **never `rejected`** |
| 2 | Browser fallback | **Per-source** approval (`requires_js` + evidence + sign-off); a global switch may only disable; concurrency 1 |
| 3 | Feed policy | **Feed-only** on normal runs; bounded listing cross-check during verification/diagnostic; fall through to sitemap → listing when the feed is unavailable |
| 4 | Dry-run | **Logs only; zero database writes**; live runs get the `ingestion_runs` record |
| 5 | Language detection | **Script-based**: Devanagari → `hi`, Latin → `en`, mixed/weak → `und` |
| 6 | Raw responses | **Metadata only**; no raw HTML persisted; sanitized fixtures for tests |
| 7 | Parser order | Structured → semantic → handler → trafilatura, **fill-missing**; confidence gate; never overwrite stronger/contradictory data |

### 20.2 Four clarifications (incorporated)

| # | Clarification | Where applied |
|---|---|---|
| 1 | Rejected-source recovery | §4.1–4.3: `rejected` is not terminal; operator-authorized `rejected → discovered` with audit reason |
| 2 | Single retry authority | §0 (D8), §6.7, §10, §14: Crawlee is the only HTTP retry layer |
| 3 | One canonical error category | §10: each failure classified exactly once; 429 only `E_RATE_LIMITED`; robots disallow `E_POLICY`; robots retrieval failure `E_ROBOTS_UNAVAILABLE` |
| 4 | Auditable verification evidence | §4.4, §5.1, §6.3, §7.5: `requests_consumed`, `request_budget`, `browser_used`, `verification_policy_version` |

**Verification policy version:** starts at `v1`; increment whenever the verification policy or its budget changes.

---

## 21. Mapping to build phases

| Architecture stage | Build phase (from `PLAN.md`) |
|---|---|
| A1 Core foundation (registry, config, DB, models, lifecycle) | Phase 0 |
| A2 Verification + discovery | Phase 1 (verification), Phase 3/4 (discovery) |
| A3 Crawling + fetching | Phase 1 (pipeline), Phase 2 (pilot) |
| A4 Extraction + processing | Phase 2–3 |
| A5 Storage + reliability | Phase 5–6 |
| A6 CLI + operations | Phase 0 onward |

---

*End of architecture. This document is the detailed design of record; `PLAN.md` references its contracts, lifecycle, component boundaries, and operational rules.*
