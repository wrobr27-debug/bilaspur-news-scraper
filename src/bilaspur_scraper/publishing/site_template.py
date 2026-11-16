"""The `localnews.developerbilaspur.in` post template.

The site is WordPress with the tagDiv "Newspaper" theme plus a custom
`lnb-` component set. This module holds the exact markup, copied
verbatim from the live posts, and renders a fact sheet into it.

Nothing here talks to the network. `wordpress.py` does that.

The fixed strings (the context and reader boxes) are stored once and
inserted as-is - they are boilerplate the site repeats on every post,
and regenerating them would only risk drift.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# fixed boilerplate - verbatim from the live site
# --------------------------------------------------------------------------

CONTEXT_HEADING = "पृष्ठभूमि"
CONTEXT_BODY = (
    "बिलासपुर की स्थानीय खबरों को समझने के लिए ज़िला प्रशासन, नगर निगम और संबंधित "
    "विभागों की आधिकारिक सूचनाओं को आधार माना जाता है। शहर की योजनाओं, सेवाओं और "
    "राजस्व से जुड़ी हर जानकारी के लिए संबंधित कार्यालय से पुष्टि करना सही कदम है।"
)

READER_HEADING = "पाठकों के लिए"
READER_BODY = (
    "इस खबर के आधार पर कोई भी निर्णय लेने से पहले संबंधित विभाग, अधिकारी या "
    "आधिकारिक वेबसाइट से जानकारी की पुष्टि करें। किसी व्यक्ति के नाम या किसी आरोप "
    "का उल्लेख यहाँ उपलब्ध रिपोर्ट के अनुसार ही किया गया है। नई और सत्यापित जानकारी "
    "मिलने पर इस खबर में संशोधन जोड़ा जाएगा।"
)

KEYPOINTS_HEADING = "मुख्य बिंदु"

#: The ad slots the theme expects. Their keys carry the publisher's own
#: ad-network identifiers; they are stored here so the renderer can
#: reproduce the live layout. `enabled` turns a slot off.
AD_SLOTS = {
    "top_desktop": {"cls": "lnb-ad lnb-ad-top lnb-ad-desktop",
                    "key": "e4a62d73cc66c5dc017556b0297d7fa1",
                    "format": "iframe", "height": 90, "width": 728},
    "top_mobile": {"cls": "lnb-ad lnb-ad-top lnb-ad-mobile",
                   "key": "f1761836d82a15e62fd1e4bc08a527b2",
                   "format": "iframe", "height": 50, "width": 320},
    "inline_mpu": {"cls": "lnb-ad lnb-ad-300 lnb-ad-inline-mpu",
                   "key": "dcccf2f46a9a6d46cd779332af5cd867",
                   "format": "iframe", "height": 250, "width": 300},
}


# --------------------------------------------------------------------------
# the fact sheet
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Keypoint:
    """One `lnb-keypoints` bullet: a short label and its detail."""
    label: str
    detail: str


@dataclass(frozen=True)
class Stat:
    """One `lnb-stats` tile: a value and what it means."""
    value: str
    label: str


@dataclass(frozen=True)
class Section:
    """One `h2` section of the body."""
    heading: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class FactSheet:
    """Everything the template needs, extracted from a transcript.

    This is the structured intermediate between the transcript and the
    HTML. Keeping it separate is what lets the facts be checked against
    the transcript before any prose is written.
    """
    headline: str
    lede: str
    keypoints: tuple[Keypoint, ...] = ()
    stats: tuple[Stat, ...] = ()
    sections: tuple[Section, ...] = ()
    source: str = "स्थानीय मीडिया रिपोर्ट"
    sensitive: bool = False
    published_at: str | None = None
    channel: str | None = None
    video_url: str | None = None
    tags: tuple[str, ...] = field(default=())


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=False)


def _ad_slot(name: str) -> str:
    """One ad slot, exactly as the live posts carry it."""
    slot = AD_SLOTS.get(name)
    if not slot:
        return ""
    params = "{}"
    return (
        f'<div class="{slot["cls"]}">'
        f'<script>atOptions = {{'
        f"'key' : '{slot['key']}', 'format' : '{slot['format']}', "
        f"'height' : {slot['height']}, 'width' : {slot['width']}, "
        f"'params' : {params}}};</script>"
        f'<script src="https://bicea.org/22/{slot["key"]}"></script>'
        f'</div>'
    )


def render_post(facts: FactSheet, *, include_ads: bool = True) -> str:
    """Render the fact sheet into the site's body HTML.

    The output is the inner HTML of `td-post-content`; WordPress stores
    it as the post content and the theme wraps it.
    """
    parts: list[str] = []

    if include_ads:
        parts.append(_ad_slot("top_desktop"))
        parts.append(_ad_slot("top_mobile"))

    # the lede - one sentence, the core fact
    parts.append(f'<p class="lnb-lede"><strong>{_esc(facts.lede)}</strong></p>')

    # key points
    if facts.keypoints:
        items = "".join(
            f"<li><strong>{_esc(k.label)}</strong> {_esc(k.detail)}</li>"
            for k in facts.keypoints)
        parts.append(
            f'<div class="lnb-keypoints">\n<h3>{_esc(KEYPOINTS_HEADING)}</h3>\n'
            f"<ul>\n{items}\n</ul>\n</div>")

    # stat tiles
    if facts.stats:
        tiles = "".join(
            f'<div class="lnb-stat">'
            f'<span class="lnb-stat-value">{_esc(s.value)}</span>'
            f'<span class="lnb-stat-label">{_esc(s.label)}</span>'
            f"</div>"
            for s in facts.stats)
        parts.append(f'<div class="lnb-stats">\n{tiles}\n</div>')

    # body sections, with an ad between the first and the rest
    for index, section in enumerate(facts.sections):
        if index == 1 and include_ads:
            parts.append(_ad_slot("inline_mpu"))
        parts.append(f"<h2>{_esc(section.heading)}</h2>")
        for para in section.paragraphs:
            parts.append(f"<p>{_esc(para)}</p>")

    # the two fixed boxes
    parts.append(
        f'<div class="lnb-context">\n<h3>{_esc(CONTEXT_HEADING)}</h3>\n'
        f"<p>{_esc(CONTEXT_BODY)}</p>\n</div>")
    parts.append(
        f'<div class="lnb-reader">\n<h3>{_esc(READER_HEADING)}</h3>\n'
        f"<p>{_esc(READER_BODY)}</p>\n</div>")

    # the source line
    parts.append(f"<p><em>स्रोत: {_esc(facts.source)}</em></p>")

    return "\n".join(p for p in parts if p)


def slugify_headline(headline: str, *, max_length: int = 72) -> str:
    """The site's URL convention: ASCII-folded, hyphenated, lowercase.

    `Chhath Puja 2026: अरपा तट पर बिलासपुर को 50 हज़ार श्रद्धालुओं के उमड़ने
    की तैयारी` becomes
    `chhath-puja-2026-arpa-tat-par-bilaspur-ko-50-hazaar-shri-dhyalon-ka`.

    A headline that is entirely in Devanagari has no ASCII characters at
    all, so the transliteration would leave nothing. In that case the
    slug is built from the words the LLM can give us in English - but
    since it cannot, a stable fallback is used: the date and a short
    hash of the headline, which keeps the URL unique and readable
    (`bilaspur-news-2026-11-16-a1b2c3`).
    """
    text = headline.strip().lower()
    # keep only ASCII word characters; everything else becomes a space
    text = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in " -_")) else " "
                   for ch in text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0].rstrip("-")

    if not text:
        # a wholly Devanagari headline: a date and a short digest keep it
        # unique without inventing an English transliteration
        import hashlib
        digest = hashlib.sha1(headline.encode("utf-8")).hexdigest()[:6]
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"bilaspur-news-{stamp}-{digest}"

    # a slug that is only a generic word is not a useful URL
    if text in ("news", "bilaspur", "update", "report"):
        import hashlib
        digest = hashlib.sha1(headline.encode("utf-8")).hexdigest()[:6]
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"{text}-{stamp}-{digest}"

    return text


def excerpt_text(facts: FactSheet) -> str:
    """The excerpt as plain text - WordPress renders it in the meta
    description and in the listing cards."""
    return re.sub(r"\s+", " ", facts.lede).strip()
