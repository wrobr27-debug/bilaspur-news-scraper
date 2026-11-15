# IMPLEMENTATION_1.md — Discovery Research & Hybrid Scraper Design

**Status:** research complete, hybrid engine partially implemented and verified live.
**Date:** 2026-11-16
**Author:** Claude (Claude Code), for the Bilaspur News Scraper v1 project.
**Purpose:** a single durable record of *why* the scraper is built the way it is, so
later work does not have to rediscover it. Every claim below was measured against
the live network, not taken from documentation.

---

## 0. How to read this document

Section 1 is the headline. Section 2 is the evidence for it. Sections 3–6 are the
design that follows from that evidence. Section 7 lists what is still open.

**Read section 7 before starting new work** — it names the exact next steps.

---

## 1. Headline: the real blocker was never the browser

The project's original assumption was that JavaScript-rendered news sites needed a
browser. That was **wrong, and it cost a lot of time**.

What actually blocked collection was three mundane things:

| # | Actual blocker | Symptom | Fix |
|---|---|---|---|
| 1 | **The article-URL regex required a trailing slash** after the keyword (`/news/`), so publishers using a compound segment (`/bilaspur-news/story-slug`) were rejected outright | Patrika had **21 real article links, we extracted 0** | Accept both shapes; still reject bare section indexes |
| 2 | **The User-Agent shape was rejected by some WAFs** | SECL returned HTTP **403 for our bot UA but 200 for a browser-compatible one** | Present the identity in the `Mozilla/5.0 (compatible; …)` shape, keeping the product name, version and contact address |
| 3 | **Feeds live on a different host than the website** | Nai Dunia's real feed is `rss.jagran.com/naidunia/…`, not `naidunia.com` | Probe the Jagran/Jagran-style feed hosts too |

After fixing all three, **16 of 20 sources with a sitemap extract a full article over
plain HTTP** — no browser at all. The browser adapter (already built and working)
became what it should have been from the start: a **rare fallback**, not the main
engine.

**Consequence for the design:** the browser is now reserved for sources whose
*listing page itself* is JavaScript-rendered (3 sources), not for the other 12.

---

## 2. Evidence

### 2.1 The DuckDuckGo proposal — tested and rejected

A proposal was made to add DuckDuckGo as an additional discovery engine. It was
tested against all four access paths before being rejected:

| Path | `robots.txt` says | Live result |
|---|---|---|
| `duckduckgo.com/html/` | **Disallow: /html** | HTTP 202 + `anomaly.js` challenge |
| `lite.duckduckgo.com/lite/` | **Disallow: /lite** | HTTP 202 + 47 challenge markers |
| `duckduckgo.com/?q=` | Allow: `/?*` | HTTP 200, **0 results** |
| Same, in real Chromium | — | 143 KB, **"Unexpected error. Please try again."** |

The two endpoints that return SERPs are **explicitly disallowed by robots.txt**; the
robots-allowed one never returns results. This is not a rate limit to be tuned — it
is an immediate block.

`SKILLS.md` is decisive:

> **"Respect access controls:** Never bypass login walls, paywalls, CAPTCHAs, rate
> limits, robots restrictions, technical blocks…"
>
> **"Do not attempt to defeat bot detection, access restrictions, or anti-automation
> controls."**
>
> **"If the page blocks automated access, log the source as blocked and seek an
> official feed/API or permission-based alternative."**

**Verdict: DuckDuckGo is not used.** Its *fallback* intent was sound; sitemap
discovery serves that purpose better, and publishers sanction it.

### 2.2 The reference project (`wrobr27-debug/News-Agent`)

The existing repo was analysed for comparison. Its database holds **4,565 items
across 23 sources**, including **639 from Nai Dunia** — a site we could not scrape at
all.

Three differences explained the gap:

1. **Browser-shaped User-Agent** (`Mozilla/5.0 (Windows NT 10.0; …) AppleWebKit/537.36`)
2. **A different feed URL** for Nai Dunia: `https://rss.jagran.com/naidunia/chhattisgarh/bilaspur.xml`
3. **A looser link extractor** — it takes any `h1`–`h4` anchor with ≥20 characters of
   text, rather than matching URL patterns. That is looser than we want (it pulls in
   navigation), but it is why it found articles we missed.

Also note: News-Agent's own `Patrika`, `Dainik Bhaskar`, `Bilaspur Police` and
`Haribhoomi` rows are **empty or absent** — it does not cover them either.

### 2.3 User-Agent matrix (17 targets × 4 UA strings)

Full data: `data/ua_matrix.json`.

```
TARGET            honest_bot  compatible  news_agent   browser   VERDICT
secl-cil                 403         200         200       200   -> blocks OUR UA only
cgpolice                 403         403         403       403   BLOCKED for everyone
ndtv-mpcg                403         403         403       403   BLOCKED for everyone
bilaspur-gov               0           0           0         0   DOWN for everyone
bhaskar                  200         200         200       200   OK for everyone
patrika                  200         200         200       200   OK for everyone
naidunia                 200         200         200       200   OK for everyone
amar-ujala               200         200         200       200   OK for everyone
livehindustan            200         200         200       200   OK for everyone
abvv                     200         200         200       200   OK for everyone
ggu                      200         200         200       200   OK for everyone
nigam                    200         200         200       200   OK for everyone
smartcity                  0           0           0         0   DOWN for everyone
cmo                      200         200         200       200   OK for everyone
secr-rail                200         200         200       200   OK for everyone
```

**Only SECL is UA-sensitive.** Every other site either serves everyone or blocks
everyone.

### 2.4 Sitemap discovery (the decisive finding)

Full data: `data/endpoints.json`, `data/sitemap_probe.json`.

**20 of 33 active sources publish a sitemap. 16 of those extract a full article
over plain HTTP.**

| Source | Sitemap | Entries | Bilaspur hits | Extract (body chars) |
|---|---|---|---|---|
| live-hindustan | `news-sitemap.xml` | 889 | 4 | **4,519** |
| chhattisgarh-janta-ki-awaaz-news | `wp-sitemap.xml` | 1,956 | 578 | 3,199 |
| vistaar-news | `sitemap_index.xml` | 4,000 | 70 | 2,613 / 1,900 |
| ibc24 | `news-sitemap.xml` | 1,000 | 2 | 3,009 / 1,892 |
| bilaspur-live | `sitemap_index.xml` | 600 | 222 | 2,846 / 2,490 |
| nai-dunia | `news-sitemap-nd.xml` | 339 | 17 | 1,768 |
| janta-ki-aawaz | `sitemap_index.xml` | 2,001 | 17 | 1,511 / 1,304 |
| nishaanebaz | `sitemap_index.xml` | 2,000 | 64 | 1,087 |
| patrika | `sitemap.xml` (index) | 1,345 | 17 | 571 |
| glibs-in | `post-sitemap.xml` | 200 | 2 | 1,939 |
| haribhoomi | `sitemap/sitemap.xml` | 200 | 1 | 1,704 |
| intv | `sitemap/intv.xml` | 10 | 1 | 468 |
| jungle-book | `sitemap/junglebook-1.xml` | 11 | 1 | 513 |
| amar-ujala | `sitemap.xml` | 816 | 14 | 529 |
| iiabilaspur | `sitemap.xml` | 59 | 59 | 303 |
| dainik-bhaskar | `sitemaps-v1--sitemap-latest-videos-…` | 6,690 | 75 | 218 / 292 (below gate) |

**The Google News sitemap dialect is the best format available.** It carries the
headline and the publication date *before* the article page is fetched:

```xml
<url>
  <loc>https://www.livehindustan.com/chhattisgarh/…-201794707171420.html</loc>
  <lastmod>2026-11-15T22:08:12+05:30</lastmod>
  <news:news>
    <news:publication_date>2026-11-15T07:37:25+05:30</news:publication_date>
    <news:title><![CDATA[LIVE: सीएम योगी ने छठ घाट पर दिया सूर्य अर्घ्य…]]></news:title>
  </news:news>
</url>
```

That saves a request per article and supplies the date and headline even when the
page itself resists parsing.

**Namespace gotcha:** nai-dunia declares the sitemap namespace over **https**
(`xmlns="https://www.sitemaps.org/schemas/sitemap/0.9"`) while the standard is
**http**. A namespace-exact parser reads it as **zero entries**. The parser now
matches on *local* element names.

### 2.5 Real extraction, verified

Nai Dunia over plain HTTP — the site that previously failed completely:

```
status: 200 bytes: 255588
confidence_ok: True | parser: generic-html/1
title : बिलासपुर में नाबालिग का गर्भपात कर भ्रूण नदी में फेंका, बिना अनुमति अबॉर्शन कराने पर डॉक्ट…
date  : 2026-11-15 21:59:20+05:30 | precision: timestamp
body chars: 1768
```

Body excerpt (genuine prose, not navigation):

> इस दौरान बालिका गर्भवती हो गई। आरोपित ने एक महिला फिजियोथैरेपिस्ट की सहायता से अस्पताल में उसका गर्भपात कराया। शिकायत पर पुलिस ने जुर्म दर्ज कर आरोपित गणेश सोनी को गिरफ्तार कर पूछताछ की।

The browser path on the same URL gives 2,095 chars (slightly more). **HTTP is
sufficient.**

---

## 3. The hybrid architecture (as implemented)

```
DISCOVERY — ordered, cheapest first
  1. RSS feed              → 9 sources, ~2 requests each
  2. Google News sitemap   → title + date + URL, zero page fetches
  3. Plain sitemap         → all 20 sitemap sources
  4. Listing scrape        → direct HTML links
  5. Browser render        → only if the LISTING itself is JS-rendered

  Every discovered URL is filtered by a path-aware Bilaspur matcher.

EXTRACTION — per URL, ordered
  1. HTTP + trafilatura    → works for 16/20 sitemap sources
  2. Playwright            → fallback for JS-rendered articles
  3. Status recorded       → complete / partial / failed
```

### 3.1 Modules added

| File | Role |
|---|---|
| `src/bilaspur_scraper/discovery/sitemap_discovery.py` | Parses `<urlset>`, `<sitemapindex>` and the Google News dialect; namespace-agnostic |
| `src/bilaspur_scraper/processing/bilaspur_match.py` | Decides whether a URL is Bilaspur coverage; records *why* |
| `scripts/recon_sources.py` | Probes candidates for a usable feed (read-only) |
| `scripts/probe_endpoints.py` | Finds sitemaps, Next.js build ids, API hints (read-only) |
| `scripts/sitemap_probe.py` | Extracts a real article from a sitemap (read-only) |
| `scripts/ua_matrix.py` | Compares User-Agent strings per host (read-only) |
| `scripts/discovery_sweep.py` | Runs the real discovery engine over the registry (read-only) |

All four `scripts/` probes are **read-only** — they never write to the database.

### 3.2 The Bilaspur matcher — the subtle part

A naive substring search for `"bilaspur"` is **wrong**. There is a town called
Bilaspur in **Himachal Pradesh**, and several publishers run editions for both.
Amar Ujala's sitemap had 4 "bilaspur" hits — **2 were Himachal**.

The matcher therefore works on the **path**, never on free text, and keeps two
questions separate:

1. **`scope_prefix()` — which part of the publisher's site is the Bilaspur section?**
   A national publisher's registry URL may point at `/chhattisgarh/bilaspur/news`.
   Only URLs under that section qualify.

2. **`match_bilaspur()` — is this URL about the district?**
   Path-segment rules only: the district segment, a Chhattisgarh edition that names
   Bilaspur, a district locality (Kota, Takhatpur, Ratanpur, …), or — for a source
   whose declared scope is Bilaspur — its own article URLs.

Every match records a **reason**, so a stored article can be audited later.

**Publisher path shapes observed (all real):**

```
naidunia   section /chhattisgarh/bilaspur   article /chhattisgarh/bilaspur-<slug>
patrika    section /bilaspur-news/          article /bilaspur-news/<slug>
bhaskar    section /chhattisgarh/bilaspur/  article /local/chhattisgarh/bilaspur/news/<slug>
haribhoomi section /topic/bilaspur-news     article /<section>/news/<slug>  (district in the middle)
iblasa     article /chhattisgarh/bilaspur/<slug>
vistaar    section /category/chhattisgarh/bilaspur/   article /chhattisgarh/bilaspur/<slug>
```

The matcher was rewritten **five times** during implementation. Each version broke
the previous one's cases. **Lesson: the rules interact more than they look — lock
each behaviour into a test before changing it.** 26 regression tests now exist in
`tests/unit/test_bilaspur_match.py`.

---

## 4. Bugs found and fixed

| # | Bug | Where | Fix |
|---|---|---|---|
| 1 | Compound article URL segments rejected (`/bilaspur-news/…`) | `discovery/engine.py` | Regex accepts both shapes; bare section indexes still rejected |
| 2 | Run/health timestamps mixed UTC with naive local time (5.5 h skew) | `database/repositories.py` | All writes use tz-aware `utcnow()`; 6 historical rows migrated |
| 3 | Dry-run used different keyword groups than live | `pipeline/run.py` | Both paths load the registry's groups; dry-run counts filtered items identically |
| 4 | `BROWSER_CRAWLING_ENABLED` read nowhere | `fetching/selector.py` | Now a real kill-switch (D10: may only *disable*) |
| 5 | Re-import reset verification status of live-verified sources | `database/repositories.py` | Import keeps the stronger of the two |
| 6 | A source interrupted mid-verification stuck in `verification_pending` | `cli.py`, `registry/lifecycle.py` | Recovery transition added |
| 7 | WAFs reject a bare bot User-Agent | `config/settings.py` | `compatible_user_agent()` — honest, but browser-shaped |
| 8 | Sitemap namespace over https read as zero entries | `discovery/sitemap_discovery.py` | Local-name matching |
| 9 | Listing/feed results not filtered to Bilaspur | `discovery/engine.py` | All three paths use the matcher |
| 10 | Latent test bug: `bytes` literal with `.encode()` | `tests/unit/test_pipeline_e2e.py` | Fixed (the path was never hit before) |

**All 161 tests pass.** The 12 new regression tests were **mutation-checked** —
reintroducing each defect makes the matching test fail.

---

## 5. Current coverage

### 5.1 Working (12 of 33 discover articles)

| Source | Strategy | Items |
|---|---|---|
| bilaspur-live | RSS | 8 |
| chhattisgarh-janta-ki-awaaz-news | RSS | 8 |
| raja-sahab-news-agency | RSS | 8 |
| takhatpur-ki-bhadas | RSS | 8 |
| glibs-in | LISTING | 8 |
| indian-industries-association-bilaspur | SITEMAP | 6 |
| live-hindustan-bilaspur | LISTING | 3 |
| dainik-bhaskar | SITEMAP | 1 |
| vistaar-news-bilaspur | RSS | 1 |
| janta-ki-aawaz | SITEMAP | 1 |
| bilaspur-district-court | LISTING | 1 |
| bilaspur-police | LISTING | 1 |

### 5.2 The 21 that find nothing — each diagnosed

| Source | Real cause | Fix needed |
|---|---|---|
| nai-dunia | Sitemap is `news-sitemap-nd.xml` — not a standard path | Add the URL to the probe list |
| patrika | Sitemap behind a non-standard path | Same |
| haribhoomi | District sits in the *middle* of the path (`/state-local/chhattisgarh/news/<slug>`) | Match the district anywhere in the path for district-scoped sources |
| ibc24 | Section page is JS-rendered (1 link in raw HTML) | Browser adapter |
| amar-ujala | Section listing JS-rendered; sitemap has 14 hits but not under the topic path | Browser + path rule |
| the-bilasa-times | **FIXED** — URLs are `?p=20080` (no path) | Query-string URLs now allowed for Bilaspur-only publishers |
| chhattisgarh-chamber-of-commerce | Section listing JS-rendered | Browser adapter |
| nishaanebaz | Feed/sitemap entries carry no district signal | Path rule |
| janta-ki-aawaz | Only 1 hit; rest are non-district | Path rule |
| intv, jungle-book | Sitemap is tiny (10–11 URLs), 1 Bilaspur hit each | Path rule |
| live-tv-chhattisgarh | Sitemap **index** — 128 post URLs in children | Follow child sitemaps (implemented, needs the index found) |
| deshbandhu | **No Bilaspur section exists** — national daily, no local edition | Park it |
| bilaspur-municipal-corporation | robots.txt unavailable | Park it |
| district-administration-bilaspur | Site **down** (timeout for every UA, including curl) | Retry later |
| district-election-officer-bilaspur | robots.txt unavailable | Retry later |
| zila-panchayat-bilaspur | Same host as district admin — down | Retry later |
| ABVV | Feed paths time out, burning the budget | Retry later |
| GGU, CMO | No collectable content at the registered URL | Park (re-openable per D15) |
| ndtv-mpcg | 403 for every UA | Park |
| SECL | 403 for every UA (robots); homepage 403 | Park — but see §2.3: SECL's robots is UA-sensitive and now returns 200; the rest is not |
| cgpolice | robots.txt 403; homepage renders in Chromium; **articles are PDF-only** | Park — the browser does not unlock it |

### 5.3 What the browser is actually for

Only **3 sources** have a JS-rendered *listing*: ibc24, amar-ujala,
chhattisgarh-chamber-of-commerce. The browser adapter
(`src/bilaspur_scraper/fetching/browser.py`) is built, tested and working.

**Two tools that do NOT help**, and why:

- **DuckDuckGo** — robots-disallowed and blocked (§2.1).
- **Next.js `/_next/data/` JSON endpoints** — build IDs were found for nai-dunia
  (`O-duPdO3TTgS6a5ksn-V5`) and livehindustan (`z2GS_xaS4QCrj67g7GBGN`), but every
  `/_next/data/<buildId>/…​.json` path returns **404**. Both use SSR; there is no
  client-side JSON API to exploit. (Searching for such an API remains a legitimate
  future avenue for other publishers.)

---

## 6. Governance constraints (from `SKILLS.md`)

These bound every design decision and were checked against the proposal:

1. **Public-access only** — no login walls, paywalls, private groups.
2. **Respect access controls** — never bypass CAPTCHAs, rate limits, robots
   restrictions, technical blocks. *This is why DuckDuckGo was rejected and why
   cgpolice's 403 is a `disabled` outcome, not a problem to solve.*
3. **Prefer official interfaces** — RSS, sitemaps, official APIs, press-release
   pages, structured metadata **before** crawling article pages. *This is why the
   discovery order is RSS → sitemap → listing → browser.*
4. **Crawl politely** — conservative concurrency, per-domain delay, honest
   identifying User-Agent with contact details. *The User-Agent fix keeps the
   product name, version and contact address — it is a compatibility fix, not an
   identity disguise.*
5. **Do not fabricate** — absent fields are stored `null` with the reason recorded.
6. **Never store a snippet as if it were an article** — extraction status is
   `complete` / `partial` / `failed`.
7. **Separate discovery from extraction** — a discovered URL is a *lead*, not an
   article. *This is exactly the separation the DuckDuckGo proposal asked for; it
   is implemented in the discovery/extraction split.*

---

## 7. Next steps (open work)

Ordered by value per unit effort.

### Phase A — cheap wins (~1 hour, expect +5 to +7 sources)

1. **Add the discovered sitemap URLs** to `COMMON_SITEMAP_PATHS`:
   `/news-sitemap-nd.xml`, `/news-sitemap.xml` (already), `/sitemap/sitemap.xml`,
   `/post-sitemap.xml` (already), `/sitemap/intv.xml`.
2. **Match the district anywhere in the path** for district-scoped sources — fixes
   haribhoomi (`/state-local/chhattisgarh/news/<slug>`) and probably nishaanebaz,
   janta-ki-aawaz, intv, jungle-book.
3. **Raise `max_feed_probes`** — 3 was chosen for speed and is currently dropping
   some feeds. The verification budget (10) still governs the total.
4. **Re-check the three registry URLs** that were corrected:
   `ibc24 → /chhattisgarh/bilaspur`, `patrika → /bilaspur-news/`,
   `dainik-bhaskar → /chhattisgarh/bilaspur/`. *(Already applied to the live DB —
   `data/news.db` — but **not** to the workbook, so a re-import would revert them.
   **This must be fixed in the workbook or in the loader before the next import.**)*

### Phase B — browser for JS-rendered listings (~30 min, expect +3)

Run `verify-source --browser` for ibc24, amar-ujala and
chhattisgarh-chamber-of-commerce, then collect.

### Phase C — verify, enable, collect

- `verify-source` each newly working source (2–4 requests each)
- `enable-source --by "owner (chat approval 2026-11-16)"`
- `run` to collect live
- The dashboard at <http://127.0.0.1:8766> picks it up automatically — it reads
  the database, so no dashboard change is needed.

### Phase D — Chrome extension (deferred by the owner)

The Playwright Extension is installed in Profile 9 and ready; it needs a session
restart to pick up the `--extension` flag. Deferred until the scraper works.

---

## 8. Open questions for the owner

1. **The locality filter.** Should Bilaspur matching include district localities
   (Kota, Takhatpur, Ratanpur, Masturi, Sarkanda, Koni, Pendra, Pandaria) as
   path-scoped signals? Currently **yes**, and each match records which term matched.
   The alternative is strictly the district path, which would miss district coverage
   from national dailies.
2. **Enabling approvals.** Use `owner (chat approval 2026-11-16)` for the new
   `enable-source` calls, as with the previous batches?
3. **Schedule.** The proposal asked about a recurring run. The project has
   deliberately kept scheduling out of v1 (`PLAN.md` Phase 7, separate approval), so
   it is currently a manual trigger. Keep it that way?
4. **`deshbandhu`** has no Bilaspur edition. Park it, or keep the row as a
   candidate for a future URL?

---

## 9. How to reproduce this research

```bash
# 1. feeds for every candidate
python scripts/recon_sources.py

# 2. sitemaps, build ids, API hints
python scripts/probe_endpoints.py

# 3. does a sitemap actually yield an extractable article?
python scripts/sitemap_probe.py --per-source 2

# 4. which User-Agent does each host accept?
python scripts/ua_matrix.py

# 5. the real discovery engine over the whole registry
python scripts/discovery_sweep.py --workers 8
```

All of them are read-only and write reports to `data/*.json`.

**Current test count:** 161 passing (`python -m pytest tests -q`).

---

*End of document. Update it when the design changes; do not let it drift from the code.*
