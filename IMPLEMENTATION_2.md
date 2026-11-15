# IMPLEMENTATION_2.md — Social/Video Source Pipeline: Findings & Design

**Status:** dataset built and loaded; transcription + generation paths verified
against the live APIs.
**Date:** 2026-11-16
**Companion to:** `IMPLEMENTATION_1.md` (website discovery research)

---

## 1. Headline

The pipeline the owner asked for — *find the accounts → transcribe the video →
generate the article → publish* — is **viable**, with one exception:

| Stage | Status | Tool |
|---|---|---|
| Find Bilaspur social/video accounts | ✅ **works** | YouTube search + official RSS (no key needed) |
| Resolve real channel IDs | ✅ **works** | channel page + RSS feed |
| Channel metadata (subs, video count) | ✅ **works** | YouTube Data API v3 |
| Get a transcript (captions present) | ✅ **works** | YouTube's own Hindi auto-captions |
| Get a transcript (no captions) | ✅ **works** | yt-dlp → ffmpeg → audio → **NVIDIA whisper** |
| **NVIDIA whisper hosted endpoint** | ⚠️ **not on this key** | see §4 |
| LLM article generation | ✅ **works** | NVIDIA `deepseek-ai/deepseek-v4.1-flash` |

**The one exception:** the NVIDIA key supplied for speech-to-text **authenticates
but exposes no speech function**. `GET /v1/models` returns 83 models and **none of
them are a speech model**; `POST /v1/audio/transcriptions` returns **404
NotFound**, and only a handful of the 83 chat models actually invoke (most return
404). Details and workarounds in §4.

---

## 2. What the dataset contains

Loaded into `data/news.db`:

| Table | Rows | What it holds |
|---|---|---|
| `social_accounts` | **153** | one row per platform account |
| `social_videos` | **272** | one row per discovered video, with the evidence that matched it |
| `media_transcripts` | 0 | ready for the transcript stage |

### The find that mattered

**The workbook already listed 46 platform accounts that were never imported.**
The registry only held `Media Publishers` (36) and `Institutions & Business` (12) —
the `Platform Accounts` sheet (46 rows) and `Public Figures` (10 rows) had been
skipped. Those are now loaded alongside 107 channels YouTube itself ranks for
Bilaspur queries.

### By platform

| Platform | Accounts | With a real channel ID |
|---|---|---|
| YouTube | 124 | **107** |
| Instagram | 13 | — |
| X | 10 | — |
| Facebook | 6 | — |

### By verification status

| Status | Count | Meaning |
|---|---|---|
| `account_identified` | 113 | found, ownership not yet confirmed |
| `search_link_only` | 24 | only a search URL is known — **a lead, not a profile** |
| `verified_official` | 10 | linked from an official site / institutional channel |
| `publisher_linked` | 4 | a publisher's own account |
| `unverified` | 2 | nothing confirms ownership |

### The local channels (with real channel IDs)

| Channel | Videos | Channel ID |
|---|---|---|
| IBC24 | 59 | `UCBc13XYipnBIBE3Ff8QaaGg` |
| Vistaar News | 18 | `UCqK_bAVnXLUUcHsYUjH_4JA` |
| IBC24 Jankarwan | 13 | `UCpDTB3TWz6lvnDpfDCFaQtA` |
| Bansal News | 9 | `UCL_3_9PhYyZXu1oexm8gtFQ` |
| Bilaspur Grand News | 9 | `UCsyOylo4H11EvJO2aj8ZZTQ` |
| TV24 | 9 | `UCNrII_N4PtlR0QeB6tfXZ9g` |
| Tirandaj | 8 | `UCnZMXWiUWMcj-WZjrSd19xQ` |
| CG BOX | 4 | `UCscgnydjcGWJcMPzK6QqrxA` |
| Bhilai Times | 5 | `UCKX_3XEl26TfCUtPDg3fRiw` |

**Bilaspur Grand News** (via the YouTube API): **14,600 subscribers, 9,130 videos**.

### Government / institutional accounts (10 `verified_official`)

Bilaspur District (`@BilaspurDist`) · Bilaspur Police (`@bsppolicecg`) · SECL
(`@secl_cil`, `@seclbilaspur`) · GGV (`@ggv_smc`) · CMO Chhattisgarh
(`@chhattisgarhcmo`, `@ChhattisgarhCMO`) · Vishnu Deo Sai (`@vishnudeosai`,
`@vishnudsai`) · Amar Agrawal (`@amaragrawalBJP`) · DEO Bilaspur.

### Sample real Bilaspur videos found

| Channel | Title | Recency |
|---|---|---|
| Tirandaj | MLA सुशांत शुक्ला के खिलाफ उबला बिलासपुर \| कांग्रेसियों के आंदोलन… | 1 mo ago |
| Bhilai Times | Bilaspur के RPF Colony में Supervisor-Gaurd की निर्मम हत्य! \| CCTV में… | 2 mo ago |
| Bilaspur Grand News | GRAND NEWS BILASPUR:स्कूटी पर सवार होकर निकले डिप्टी सीएम… | 4 mo ago |
| Takhatpur Ki Bhadas | 🚨 तखतपुर बस स्टैंड के पास कार में अचानक लगी आग… | 2023-11-04 |
| CG BOX | Chhattisgarh bilaspur atal awas fire accident में इस तरह बची जानें | 3 wk ago |

---

## 3. How the discovery works (no API key required)

Three keyless, first-party paths:

1. **YouTube search** (`scripts/youtube_search.py`) — the results page is
   server-rendered and carries `videoRenderer` objects with the video id, title,
   channel id and channel name. Pairing those gives a channel list **derived from
   actual Bilaspur coverage** rather than a hand-typed directory. 18 built-in
   queries (English and Hindi) → **337 videos, 107 channels**.

2. **Official channel RSS** (`scripts/youtube_discovery.py`) — every channel
   publishes `/feeds/videos.xml?channel_id=UC…`. No key, sanctioned, and it
   returns the latest 15 videos with titles and dates.

3. **Channel page** — the `channelId` is readable from the public page, so a
   handle resolves without the Data API.

**Gotchas found:**
- A search results page contains *other* renderer types first (a "shelf"), so the
  parser must require `title` to appear **before** `ownerText` in the chunk.
- The workbook lists several YouTube rows as **search-result URLs**
  (`youtube.com/results?search_query=…`). Those are not channels; the dataset
  records them as `search_link_only`.
- `og:title` is the reliable channel-name source; `"title"` in the embedded JSON
  returns "Home".

---

## 4. The NVIDIA speech-to-text finding (important)

### What was tested

| Attempt | Result |
|---|---|
| `GET integrate.api.nvidia.com/v1/models` | **200**, 83 models — **0 speech models** |
| `POST /v1/audio/transcriptions` (curl, mp3 and wav) | **404 Not Found** |
| same via the OpenAI SDK (`client.audio.transcriptions.create`) | **404 NotFound** |
| `nvidia/whisper-large-v3`, `openai/whisper-large-v3`, `…-turbo` | all 404 |
| `ai.api.nvidia.com`, `build.nvidia.com/v1/…`, `api.nvidia.com` | 404 / Next.js 500 page |
| per-model hosts (`openai-whisper-large-v3.api.nvidia.com`) | **DNS does not resolve** |
| `api.nvcf.nvidia.com/v2/functions/…` | 404 — no function routes exposed |

**Conclusion: the key authenticates (a chat request returns a real model-lifecycle
error, not 401) but this account has no speech-to-text function provisioned.**
The 404 is on the *route*, before the model is even considered.

Also note: the model card says **"Supported Operating System(s): Linux"** and
**"Input Format: Linear PCM 16-bit 1 channel"** — so even once the route is
found, the payload is 16 kHz mono PCM, not MP3.

### What *does* work on this key

Only some of the 83 listed chat models actually invoke. Verified working:
**`deepseek-ai/deepseek-v4.1-flash`**. Others tested returned 404.

**Gotcha:** it is a reasoning model. A 400-token request returns
`finish_reason="length"` with `content=None` — it spends the budget thinking.
**Use `max_tokens=3000`**, after which it returns a clean answer.

### Verified generation output

Transcript (from YouTube's own captions, a real Bilaspur bulletin):

> बिलासपुर। सीपत वन परिक्षेत्र में वन विभाग के सरकारी दफ्तर में जाम छलकने की
> शिकायत मिली है… वन परिक्षेत्र अधिकारी ने कहा कि जल निकासी की कार्रवाई शुरू कर दी
> गई है।

Generated article:

> बिलासपुर: सीपत वन परिक्षेत्र में वन विभाग के सरकारी दफ्तर में जलभराव की शिकायत
> मिली है। स्थानीय लोगों ने कर्मचारियों की लापरवाही से पानी जमा होने की बात कही। वन
> परिक्षेत्र अधिकारी ने कहा कि जल निकासी की कार्रवाई शुरू कर दी गई है।

It corrected the speech-recognition error ("जाम छलकने" → "जलभराव") and invented
nothing.

### The workaround that needs no NVIDIA speech API

**YouTube's own Hindi auto-captions** already cover most of the corpus and need
no key and no download:

- a real Bilaspur news video returned **2,007 characters** of Hindi transcript
  and **152 caption segments**
- the transcript correctly mentions *"सीपत बिलासपुर"* and *"वन परिक्षेत्र अधिकारी"*

For videos **without** captions: yt-dlp → ffmpeg → MP3 (verified, 1.9 MB for a
2m46s video) → then either a local Whisper or an NVIDIA key that has speech
enabled.

---

## 5. Governance: what was authorised, and the rights question

The owner **explicitly authorised** full article generation and publishing
("Full article generation", "we will publish that particular article to the
website"), which opens the `SKILLS.md` gate — the charter permits these actions
*"unless a separate, explicit task authorizes those actions"*.

**That authorisation was given for this project. It does not transfer
automatically to the next person, and it does not settle the rights question.**

### The rights position, stated plainly

The transcripts are **the publishers' copyrighted content**. Speech-to-text
produces a verbatim derivative; an LLM writing from it produces a further
derivative. Publishing that under the site's own name is a rights question, not
just a technical one.

Concretely, the target publishers — The Bilasa Times, Takhatpur Ki Bhadas,
Bilaspur Grand News, Tirandaj — are **competing local news outlets**, not the
owner's own content. A site that republishes their bulletins verbatim under its
own masthead is the classic case publishers object to.

**Minimum safeguards, if publishing proceeds:**

1. **Attribute every item** — channel name, video title, and a link to the
   original. Never publish a Bilaspur bulletin under the site's own byline.
2. **Quote, do not copy** — the generated brief should be a summary with the
   source visible, not a full reproduction of the transcript.
3. **Do not republish government notices under a news voice** — Bilaspur
   District, Bilaspur Police, SECL and GGV produce official notices; those are
   official documents, and republishing them alters their authority.
4. **Sensitive material needs human review** — `SKILLS.md` §10 requires crime,
   accident, health and election items to be marked `needs_review` and never
   published automatically. A video bulletin about a crime allegation is exactly
   that.
5. **NVIDIA's own terms** — the build.nvidia.com page carries a notice that
   *"no confidential information or personal data (such as voices or faces of
   people)"* be uploaded. A voice is personal data. **Transcribing identifiable
   Bilaspur residents' speech and uploading it to NVIDIA's cloud is a data
   question the owner should settle before bulk transcription.**

---

## 6. Files

| File | Role |
|---|---|
| `src/bilaspur_scraper/database/media_models.py` | `social_accounts`, `social_videos`, `media_transcripts` |
| `scripts/youtube_search.py` | search YouTube → channels + videos (keyless) |
| `scripts/youtube_discovery.py` | resolve handles → channel ids, read RSS (keyless) |
| `scripts/build_social_dataset.py` | merge workbook + search → dataset → SQLite |
| `tests/unit/test_social_dataset.py` | 26 tests: URL classification, status mapping, id stability, uniqueness |

### Keys

Stored in the **gitignored `.env`**, never in code:

```
NVIDIA_API_KEY=...        # 83 LLMs; no speech function on this account
YOUTUBE_API_KEY=...       # Data API v3 - full channel metadata + search
```

> **Rotate both keys.** They were pasted into a chat transcript, so they should be
> considered exposed. The `.env` is gitignored and was never committed.

---

## 7. Next steps

### Phase A — transcripts (no NVIDIA speech API needed)

1. Fetch YouTube's Hindi auto-captions for the 272 videos → `media_transcripts`
   with `method="youtube_captions"`.
2. Mark the videos with no captions `needs_review` for the audio path.
3. Record `language` and `language_source` honestly.

### Phase B — the audio fallback (needs a decision)

Pick one:
- **a local Whisper** (`faster-whisper`, `small` or `medium`) — no key, no cost,
  no upload, Hindi-capable; needs a ~0.5–1.5 GB model download
- **an NVIDIA key with speech enabled** — the owner may need to request the
  speech function for the account, or use a different account
- **skip** — captions alone may cover most of the corpus

### Phase C — article generation

`deepseek-ai/deepseek-v4.1-flash`, `max_tokens=3000`, with a prompt that forbids
inventing facts and requires the source to be named. Store the generated text
**alongside** the transcript, marked as machine-generated, with the model id.

### Phase D — publishing

Only after C, and only with the safeguards in §5 agreed.

---

## 8. Open questions for the owner

1. **Rights/attribution** — confirm the safeguards in §5, in particular whether
   the published item links back to the original channel and quotes rather than
   reproduces.
2. **NVIDIA voice upload** — the terms ask that no identifiable voices are
   uploaded. Bulk-transcribing local residents' speech to NVIDIA's cloud needs
   an explicit decision; a local Whisper avoids it.
3. **Speech access** — should the owner request speech-to-text provisioning on
   the NVIDIA account, or run Whisper locally?
4. **Scope** — all 153 accounts, or only the `verified_official` +
   `publisher_linked` ones (14) plus the local channels?
5. **YouTube API quota** — the key allows ~10,000 units/day; a full sweep of 272
   videos' metadata is well within it, but a daily re-crawl of every channel's
   uploads should be checked against quota.

---

*End of document. Update it when the design changes; do not let it drift from the code.*
