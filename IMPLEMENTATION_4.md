# IMPLEMENTATION_4.md — The Video-News Pipeline

**Status:** implemented and verified end to end against live videos.
**Date:** 2026-11-16
**Companion to:** `IMPLEMENTATION_2.md` (social dataset), `IMPLEMENTATION_3.md` (WordPress template)

---

## 1. What the system does

```
YouTube video
    │
    ├── transcript ──────────── the publisher's own caption track
    │                           (yt-dlp → json3), falling back to
    │                           local Whisper over the downloaded audio
    │
    ├── fact sheet ─────────── minimax-m3.1-flash extracts
    │                           {headline, lede, keypoints, stats,
    │                           sections, source} as JSON
    │
    ├── fact check ─────────── every number and proper noun in the
    │                           sheet must appear in the transcript
    │
    ├── news article ───────── the site's own lnb- template
    │                           (lede, keypoints, stats, sections,
    │                            पृष्ठभूमि, पाठकों के लिए, स्रोत)
    │
    ├── featured image ──────── 1200x675, generated
    │
    └── WordPress draft ─────── media upload + post create
```

Run it:

```bash
python scripts/run_video_news_pipeline.py --limit 5 --dry-run   # no posting
python scripts/run_video_news_pipeline.py --limit 5             # create drafts
python scripts/run_video_news_pipeline.py --limit 5 --publish   # publish (non-sensitive)
```

---

## 2. Transcription

**Primary — the publisher's own caption track.** `yt-dlp` fetches the
`json3` subtitle file (Hindi first, then `hi-en`, then `en`). This is the
publisher's own words, needs no key and no download, and covers most
bulletins. A real Bilaspur bulletin yielded **2,007 characters across
152 caption segments**.

**Fallback — local Whisper.** When a video has no caption track:

```
yt-dlp → ffmpeg → 16 kHz mono MP3 → faster-whisper → text
```

**Why a separate interpreter.** `faster-whisper` needs `pyyaml`, and
**pyyaml has no wheel for Python 3.15** — it only builds from source and
fails. The project runs on 3.15; the transcription runs on the
**Python 3.11** interpreter that is also installed, where every wheel
exists. `scripts/media_pipeline.py::_transcribe_subprocess` shells out to
it and decodes the output as UTF-8 explicitly (the parent's default
`cp1252` mangles the Hindi and breaks the JSON).

Measured on a real 166-second Bilaspur bulletin: **63 segments**,
CTranslate2 `int8` on CPU. The `small` model loads in ~100 s the first
time, then a 60-second clip transcribes in **1.8 s**.

**A trap worth knowing:** a video can have *no caption track and no
narration* — a 60-second photo slideshow with background music.
Whisper correctly returns nothing. That is reported as `no_speech`,
not as a failure.

**Model:** `small` by default. `medium` is noticeably better on Hindi at
roughly three times the CPU time; set it with
`WHISPER_MODEL=medium`.

---

## 3. Article generation

**Model:** `minimaxai/minimax-m3.1-flash` on NVIDIA NIM.

It was chosen over the deepseek reasoning model, which spends minutes of
its budget thinking before emitting JSON — measured at a 90-second
timeout on the fact-extraction shape — while minimax answers the same
request in about 11 seconds with clean JSON.

**The hosted endpoint is flaky.** The same request intermittently answers
HTTP 503 ("content safety screening is temporarily unavailable" or
"Service temporarily overloaded"), and the failure is *not* correlated
with the content: an identical payload was measured succeeding and
failing across consecutive calls. A measured run of eight calls gave
**5 successes**. So the retry rotates through models rather than
hammering one:

```
minimaxai/minimax-m3.1-flash        (primary)
nvidia/nemotron-3.5-lightning-30b-a3b
mistralai/mistral-large-4-1t-a52b
nvidia/nemotron-3-ultra-550b-a55b
```

All four were verified as callable on this account. (The model list
changes: `meta/llama-3.1-nemotron-51b-instruct` returned HTTP 410
"end of life" during the build, so the fallback list is checked against
the live catalogue before it is relied on.)

**Transcript length** is capped at 8,000 characters. A live stream runs
for hours and produces a transcript of hundreds of thousands of
characters, which no model should be asked to summarise in one call —
and a news brief covers one story, not a day's broadcast. When the cap
bites, the most recent part is kept: for a bulletin, the payload at the
end is the current story.

---

## 4. Fact checking

Mechanical, not a judgement. Every number and proper noun in the
generated sheet must appear in the transcript it came from; anything
else is flagged and the post is **not** written.

```
flag: number not in transcript: 45
flag: proper noun not in transcript: Rajesh
flag: place not in transcript: रायपुर
```

Labels such as `क्या है मामला:` are exempt — they are the writer's
structure, not a claim.

A story is also marked **sensitive** when it concerns crime, accident,
health, legal matters, elections, communal tension or disaster. A
sensitive story is never published automatically; it is left as a draft
for a human, per `SKILLS.md` §10.

---

## 5. Featured image

**Generated, not borrowed.** The site's own images credit a photographer
and a licence (`संपादकीय ग्राफिक: … (फाइल फोटो: … / Wikimedia Commons,
CC BY 3.0)`). A YouTube thumbnail is the publisher's copyrighted image,
and republishing it under our masthead is a rights problem at scale — so
the default is an **original 1200×675 typographic card** built from the
headline and the facts.

`src/bilaspur_scraper/publishing/image_providers.py` offers three
providers, tried in order:

| Provider | Needs | Status |
|---|---|---|
| `local` | nothing | **works** — Pillow + the Devanagari font |
| `minimax` | `MINIMAX_API_KEY` | the API exists (`image-01`) but needs a MiniMax secret key |
| `nvidia` | `NVIDIA_IMAGE_MODEL` | the route exists (`/v1/images/generations`) but **no image model is enabled** on this account — every model returns 404 |

The `nvidia` provider reports itself unavailable rather than failing the
run, so the local card is used.

---

## 6. Publishing

WordPress REST API at `localnews.developerbilaspur.in`.

```
POST /wp-json/wp/v2/media      multipart: file, title, alt_text
POST /wp-json/wp/v2/posts      JSON: title, content, excerpt, slug,
                                        categories, featured_media,
                                        status, comment_status
POST /wp-json/wp/v2/posts/{id}  {"status": "publish"}   ← separate step
```

**Posts are created as drafts.** `SKILLS.md` §16 lists posting to
WordPress as something that must not happen without separate
authorisation, and §10 requires human corroboration for sensitive
material. The pipeline is automatic up to the draft; publishing is a
separate, explicit call.

**Credentials** (`WORDPRESS_APP_PASSWORD`, `WORDPRESS_USERNAME`,
`WORDPRESS_BASE_URL`) go in the gitignored `.env`. They are **not
configured yet** — the run stops before posting until they are.

---

## 7. What was verified, and what is not

**Verified end to end** (dry-run, live videos, real network):

| Video | Transcript | Facts | Fact check | Article | Image |
|---|---|---|---|---|---|
| Ambikapur elephant live stream | 12,000 chars (truncated from 16,859 segments) | ✅ | ✅ OK | ✅ | ✅ |
| Bilaspur Chhath Puja | 5,499 chars (165 segments) | ✅ | ✅ OK (sensitive) | ✅ | ✅ |
| SIPAT forest office bulletin | 2,007 chars via local Whisper | ✅ | ✅ OK | ✅ | ✅ |

**Not yet verified:** the WordPress POST. It needs the application
password.

---

## 8. Open items

1. **WordPress credentials** — needed to post. An application password
   for `admin`, not the account password.
2. **NVIDIA image model** — the route exists but no model is enabled.
   Enable one, or accept the generated typographic card (which is
   rights-clean and matches the site's design intent).
3. **MiniMax API secret key** — enables MiniMax's own `image-01`
   generation.
4. **Whisper model size** — `small` now; `medium` is better on Hindi.
5. **Batch size** — the fact extraction takes ~11–30 s per video
   because of the flaky endpoint and the rotating retries. A batch of 50
   would take roughly 30 minutes.

---

*End of document. The measured facts here (model list, timings, the
pyyaml/Python-3.15 constraint) go stale; re-check them before relying
on them.*
