# IMPLEMENTATION_3.md — WordPress Post Generation for localnews.developerbilaspur.in

**Status:** design complete, measured against the live site.
**Date:** 2026-11-16
**Companion to:** `IMPLEMENTATION_1.md` (website discovery), `IMPLEMENTATION_2.md` (social/video)

---

## 1. What the site actually is

`https://localnews.developerbilaspur.in/` is a **WordPress site using the tagDiv
"Newspaper" theme**, with a **custom `lnb-` component set** layered on top.

Everything below was measured from the live site, not guessed:

| Fact | Value | Source |
|---|---|---|
| REST API | open, no auth for reads | `GET /wp-json/wp/v2/posts/645` → 200 |
| Theme | tagDiv Newspaper (`td-` prefixed classes) | post HTML |
| Custom components | `lnb-lede`, `lnb-keypoints`, `lnb-stats`, `lnb-context`, `lnb-reader`, `lnb-ad-*` | 15 recent posts |
| Featured image | **1200×675 JPEG (16:9)**, ~239 KB | `lnb-645-featured-photo-v5.jpg` |
| Categories | `bilaspur` (32), `breaking` (70), plus ~10 others | `/wp-json/wp/v2/categories` |
| Author | `admin` (id 1) | post JSON |
| Comments | `closed` on published posts | post JSON |
| Post length | 2,400–3,100 chars of body HTML | 15 recent posts |

**The template is remarkably consistent** — all 15 recent posts use the same
components in the same order. That is what makes generation safe.

---

## 2. The exact post anatomy

Captured verbatim from post 645 (the sample you linked) and confirmed against
posts 648–681.

```
<h1 class="entry-title">                      ← theme renders the title
<p class="td-post-sub-title">                  ← optional subtitle
<div class="td-module-meta-info">              ← theme renders author/date
  …

<div class="td-post-content tagdiv-type">      ← THE BODY WE GENERATE
  <div class="lnb-ad lnb-ad-top lnb-ad-desktop">…</div>       ← ad slot
  <div class="lnb-ad lnb-ad-top lnb-ad-mobile">…</div>        ← ad slot

  <p class="lnb-lede"><strong>ONE-SENTENCE SUMMARY.</strong></p>

  <div class="lnb-keypoints">
    <h3>मुख्य बिंदु</h3>
    <ul>
      <li><strong>LABEL:</strong> detail.</li>
      <li><strong>LABEL:</strong> detail.</li>
    </ul>
  </div>

  <div class="lnb-stats">
    <div class="lnb-stat">
      <span class="lnb-stat-value">1000 मीटर</span>
      <span class="lnb-stat-label">लंबा घाट तैयार किया गया है</span>
    </div>
    …1 to 3 of these…
  </div>

  <h2>पहला उप-शीर्षक</h2>
  <p>शरीर का पहला अनुच्छेद।</p>

  <div class="lnb-ad lnb-ad-300 lnb-ad-inline-mpu">…</div>    ← mid-article ad

  <h2>दूसरा उप-शीर्षक</h2>
  <p>शरीर का दूसरा अनुच्छेद।</p>

  <div class="lnb-context">
    <h3>पृष्ठभूमि</h3>
    <p>स्थानीय संदर्भ — ज़िला प्रशासन, नगर निगम आदि की आधिकारिक सूचनाओं
       को आधार माना जाता है…</p>
  </div>

  <div class="lnb-reader">
    <h3>पाठकों के लिए</h3>
    <p>पुष्टि करें, अस्पष्ट/आरोप यहाँ उपलब्ध रिपोर्ट के अनुसार…</p>
  </div>

  <p><em>स्रोत: SOURCE ATTRIBUTION</em></p>
</div>
```

### The four fixed strings

`lnb-context` and `lnb-reader` are **boilerplate that never changes** across
posts. They are stored once and reused verbatim:

**पृष्ठभूमि**
> बिलासपुर की स्थानीय खबरों को समझने के लिए ज़िला प्रशासन, नगर निगम और संबंधित
> विभागों की आधिकारिक सूचनाओं को आधार माना जाता है। शहर की योजनाओं, सेवाओं और
> राजस्व से जुड़ी हर जानकारी के लिए संबंधित कार्यालय से पुष्टि करना सही कदम है।

**पाठकों के लिए**
> इस खबर के आधार पर कोई भी निर्णय लेने से पहले संबंधित विभाग, अधिकारी या
> आधिकारिक वेबसाइट से जानकारी की पुष्टि करें। किसी व्यक्ति के नाम या किसी आरोप
> का उल्लेख यहाँ उपलब्ध रिपोर्ट के अनुसार ही किया गया है। नई और सत्यापित जानकारी
> मिलने पर इस खबर में संशोधन जोड़ा जाएगा।

**स्रोत** — varies by post:
- `स्रोत: बिलासपुर ज़िला प्रशासन / स्थानीय मीडिया रिपोर्ट` (post 645)
- `स्रोत: स्थानीय मीडिया रिपोर्ट` (post 660, a media-sourced story)

---

## 3. The data flow

```
YouTube video
    ↓  caption track (publisher's own words)
transcript
    ↓  LLM: extract the FACTS, not prose
fact sheet  { what, where, when, who, numbers, source }
    ↓  fact-check: every number/name/place vs. the transcript
    ↓  LLM: write into the site's structure
    ↓  render the lnb- HTML template
WordPress-ready HTML
    ↓  REST API POST (media → post)
live article on localnews.developerbilaspur.in
```

### Why two LLM passes, not one

One pass ("write an article") produces prose but **loses the numbers and dates**,
which are exactly what the template's `lnb-stats` and `lnb-keypoints` need.
So:

**Pass 1 — fact extraction.** Pull a structured sheet out of the transcript:
- `headline` — a factual Hindi headline, no editorialising
- `lede` — one sentence, the core fact
- `keypoints` — 2–3 `{label, detail}` pairs
- `stats` — 1–3 `{value, label}` pairs (numbers only)
- `sections` — 2–4 `{heading, paragraphs}`
- `source` — where the claim comes from
- `sensitive` — crime / accident / health / election → needs review

**Pass 2 — rendering.** Turn that sheet into the template's HTML, with the
boilerplate inserted verbatim.

Pass 1's output is **checkable**: every number in it must appear in the
transcript. That is the same mechanical check already built
(`scripts/media_pipeline.py::fact_check`).

---

## 4. The featured image

**The site uses a designed editorial graphic, not a video thumbnail.**

The sample's featured image (`lnb-645-featured-photo-v5.jpg`) is:
- **1200×675** (16:9), JPEG, ~239 KB
- named `lnb-{post_id}-featured-photo-v5.jpg`
- `alt` text = `संपादकीय ग्राफिक: {headline} (फाइल फोटो: {photographer} / Wikimedia Commons, CC BY 3.0)`
- `title` = `Featured photo graphic v5 — post {post_id}`

**Important: the site's own images carry an explicit licence attribution in the
alt text.** It is a designed graphic over a Creative-Commons photo.

Three options, and the honest trade-off between them:

| Option | How | Rights | Quality |
|---|---|---|---|
| **A. YouTube thumbnail** | `https://i.ytimg.com/vi/{id}/maxresdefault.jpg` | **Publisher's copyrighted thumbnail — republishing it is a rights problem** | Sharp, recognisable, but not editorial |
| **B. Download + compose** | take the thumbnail, overlay a Hindi headline band, crop to 1200×675 | Same rights problem, plus it is a derivative | Matches the site's look |
| **C. Generate an original graphic** | render our own 1200×675 card from the facts — headline, place, date, source attribution — and **no third-party photo** | Clean: we publish only our own typography and facts | Fully original, on-brand, no rights issue |

**Recommendation: C.** It reproduces the site's *design intent* (a typographic
editorial card) without touching anyone else's image. It is also the only
option that is rights-clean at scale, which matters when the system publishes
daily, automatically, from 150+ channels.

Option A or B is fine **if** the source video is published under a licence that
permures reuse — but that has to be checked per video, and YouTube's default
terms do not permit it. The pipeline should therefore treat the featured image
as **generated by default**, with a thumbnail fallback only when the video's
licence is explicitly known to permit reuse.

---

## 5. Publishing via the REST API

Reads are open; **writes need an application password.**

```
POST https://localnews.developerbilaspur.in/wp-json/wp/v2/media
     ?Authorization=Basic base64(user:app_password)
     (multipart: file=@image.jpg, title=..., alt_text=..., caption=...)

POST https://localnews.developerbilaspur.in/wp-json/wp/v2/posts
     ?Authorization=Basic base64(user:app_password)
     (application/json)
     {
       "title": "...", "content": "<the lnb- HTML>",
       "excerpt": "<the lede, plain text>",
       "slug": "chhath-puja-arpa-river-bilaspur-ghat",
       "categories": [32, 70],
       "featured_media": 646,
       "status": "draft",
       "comment_status": "closed",
       "ping_status": "closed"
     }
```

### The decisions encoded here

**`status: "draft"` — always, at first.** `SKILLS.md` §10 requires human
corroboration before editorial use for crime, accidents, health and election
material, and §16 lists "post to WordPress" as something that must not happen
without separate authorisation. Publishing as **draft** keeps the whole
pipeline automatic while leaving the publish decision with a human. A
promotion step (draft → publish) is one API call and stays manual.

**`excerpt`** is the lede, in plain text. WordPress renders it in the meta
description and in the listing cards.

**`slug`** is generated from the headline, ASCII-folded, so the URL matches the
site's convention: `chhath-puja-arpa-river-bilaspur-ghat`.

**`featured_media`** is the id returned by the media upload. The theme renders
it into `td-post-featured-image` with its own srcset, so we never hand-write
an `<img>` tag.

**`comment_status: "closed"`** matches every published post on the site.

### Category mapping

| Slug | Id | When |
|---|---|---|
| `bilaspur` | 32 | always — it is a Bilaspur site |
| `breaking` | 70 | urgent/incident stories |
| others | 34–43 | read from the site's category list at runtime |

The category list must be **fetched at runtime**, not hardcoded, so a new
category on the site is picked up automatically.

---

## 6. What the generator must guarantee

| Guarantee | How |
|---|---|
| No invented facts | Every number, name and place in the fact sheet must appear in the transcript — `fact_check` already does this |
| Claims attributed | The prompt requires "X ने बताया कि…"; the `lnb-reader` box reinforces it |
| Original Hindi preserved | The brief keeps the transcript's place and person names; nothing is translated |
| Boilerplate verbatim | `lnb-context` / `lnb-reader` come from a stored constant, never regenerated |
| No third-party images | Featured image is generated by default |
| Sensitive → draft | Crime/accident/health/election always `status: draft`, flagged for review |
| Sourced | `स्रोत:` line names the channel and, where applicable, the official body |
| Deduped | A video already processed is not re-published; the dataset's `content_hash` and the post's slug are checked |

---

## 7. Components to build

| File | Role |
|---|---|
| `src/bilaspur_scraper/publishing/site_template.py` | The `lnb-` template: the fixed strings, the ad slots, the renderer |
| `src/bilaspur_scraper/publishing/fact_sheet.py` | The typed fact sheet (Pydantic) + the extraction prompt |
| `src/bilaspur_scraper/publishing/wordpress.py` | REST client: media upload, post create, draft→publish, category lookup |
| `src/bilaspur_scraper/publishing/featured_image.py` | Generate the 1200×675 editorial card |
| `src/bilaspur_scraper/publishing/pipeline.py` | The orchestrator: video → transcript → facts → HTML → draft |
| `tests/unit/test_publishing.py` | Template fidelity, fact extraction, slug, dedup, draft guarantee |

---

## 8. Open questions for the owner

1. **WordPress credentials** — an application password for `admin`, and the
   base URL. It goes in the gitignored `.env` as `WORDPRESS_APP_PASSWORD`,
   never in code.
2. **Draft or publish?** The pipeline is designed to publish as **draft** and
   leave the final call to a human. Confirm, or say if you want it to publish
   automatically for non-sensitive stories.
3. **Featured image** — generated graphic (recommended, rights-clean) or the
   YouTube thumbnail (rights need checking per video)?
4. **The graphic's look** — the sample is a photo with a headline overlay. A
   pure-typographic card is rights-clean but a different look. Worth a
   reference image or two before building?
5. **Source attribution wording** — `स्रोत: बिलासपुर ज़िला प्रशासन / स्थानीय मीडिया
   रिपोर्ट` vs `स्रोत: स्थानीय मीडिया रिपोर्ट`. Which for a video-sourced
   story — should the **channel name** appear (e.g. `स्रोत: IBC24 रिपोर्ट /
   स्थानीय मीडिया`)?
6. **Categories** — should a video-sourced story get `bilaspur` only, or
   `bilaspur` + `breaking` when it is an incident?

---

*End of document. The template strings here are verbatim from the live site;
keep them in sync with `site_template.py` when the theme changes.*
