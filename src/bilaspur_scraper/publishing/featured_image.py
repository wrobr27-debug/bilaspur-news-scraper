"""Generate the site's editorial featured image.

The live site uses a 1200×675 graphic. This module produces one from
the **facts alone** - no third-party photograph - so the published image
carries only our own typography and our own reporting.

Why generate rather than reuse a video thumbnail:

* the site's own images credit a photographer and a licence
  (`संपादकीय ग्राफिक: … (फाइल फोटो: … / Wikimedia Commons, CC BY 3.0)`),
  so its featured images are *designed graphics over licensed photos*;
* a YouTube thumbnail is the publisher's copyrighted image, and
  republishing it under our masthead is a rights problem at scale;
* an original card reproduces the design intent - a typographic editorial
  card - without touching anyone else's picture.

The output matches the site's dimensions and naming convention:
`lnb-{post_id}-featured-photo.jpg`, 1200×675 JPEG.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WIDTH = 1200
HEIGHT = 675

#: The site's palette, read off the live theme.
INK = (18, 20, 26)
PAPER = (247, 244, 238)
ACCENT = (196, 58, 43)       # the deep red used for the category band
RULE = (214, 206, 194)
MUTED = (104, 98, 90)

#: Windows' Devanagari face. Pillow cannot shape Devanagari by itself, so
#: the text is rendered through the font and the browser-independent
#: shaping is done by `uharfbuzz` when available; otherwise the image
#: falls back to a Latin transliteration-safe render.
_DEVANAGARI_FONTS = (
    r"C:\Windows\Fonts\Nirmala.ttc",
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\Mangal.ttf",
    r"C:\Windows\Fonts\Shruti.ttf",
)

_LATIN_FONT = r"C:\Windows\Fonts\arialbd.ttf"


def _find_font(candidates) -> Optional[str]:
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def _load_font(path: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not load font %s: %s", path, exc)
        return None


def _shape(text: str, font_path: str, size: int) -> list:
    """Render Devanagari text with correct conjuncts and matras.

    Pillow draws glyphs one at a time and does not apply the
    Devanagari shaping rules, so the text comes out as separated
    letters. `uharfbuzz` (HarfBuzz) does the shaping; when it is not
    installed the caller falls back to an unshaped render.
    """
    try:
        import uharfbuzz as hb
    except ImportError:
        return None
    from PIL import ImageFont
    try:
        blob = hb.Blob.from_file_path(font_path)
        face = hb.Face(blob)
        font = hb.Font(face)
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(font, buf)
        return list(buf)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Devanagari shaping failed: %s", exc)
        return None


def render_featured_image(*, headline: str, channel: str | None = None,
                          source: str | None = None,
                          published_label: str | None = None,
                          output_path: Path | None = None,
                          post_id: int | None = None) -> Path:
    """Render the 1200×675 editorial card and return its path."""
    from PIL import Image, ImageDraw

    out = output_path or Path(
        f"lnb-{post_id}-featured-photo.jpg" if post_id else "lnb-featured-photo.jpg")

    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)

    # a paper texture: faint warm speckle so the card does not read as a
    # flat rectangle of colour
    for x in range(0, WIDTH, 7):
        tone = 244 if (x // 7) % 3 == 0 else PAPER[0]
        draw.line([(x, 0), (x, HEIGHT)], fill=(tone, tone - 3, tone - 8))

    margin = 72

    # the category band at the top
    draw.rectangle([0, 0, WIDTH, 12], fill=ACCENT)

    # the masthead line
    label = "लोकल न्यूज़ बिलासपुर"
    label_font = _load_font(_find_font(_DEVANAGARI_FONTS) or _LATIN_FONT, 30)
    if label_font:
        draw.text((margin, 52), label, fill=MUTED, font=label_font)

    # the date / source line, right-aligned
    meta = published_label or ""
    if channel:
        meta = f"{channel}  ·  {meta}" if meta else channel
    meta_font = _load_font(_find_font(_DEVANAGARI_FONTS) or _LATIN_FONT, 26)
    if meta_font and meta:
        draw.text((margin, 96), meta, fill=MUTED, font=meta_font)

    # a rule under the masthead
    draw.line([(margin, 142), (WIDTH - margin, 142)], fill=RULE, width=2)

    # the headline - the largest element
    head_font_path = _find_font(_DEVANAGARI_FONTS) or _LATIN_FONT
    head_font = _load_font(head_font_path, 58)
    y = 190
    if head_font:
        if _has_devanagari(headline):
            glyphs = _shape(headline, head_font_path, 58)
            if glyphs:
                # draw the shaped glyphs run by run
                x = margin
                line_h = 76
                current = []
                runs = []
                for info in glyphs:
                    if info.codepoint in (10, 13):  # newline
                        runs.append(current); current = []
                        continue
                    current.append(info)
                runs.append(current)
                for run in runs:
                    if x > WIDTH - margin - 60:
                        x = margin; y += line_h
                    for info in run:
                        try:
                            from PIL import ImageFont
                            f = ImageFont.truetype(head_font_path, 58)
                            draw.text((x, y), chr(info.codepoint),
                                      fill=INK, font=f,
                                      features=None)
                            x += f.getlength(chr(info.codepoint))
                        except Exception:  # noqa: BLE001
                            pass
                    y += line_h
            else:
                y = _draw_wrapped(draw, headline, head_font, margin, y,
                                  WIDTH - margin, 76, INK)
        else:
            y = _draw_wrapped(draw, headline, head_font, margin, y,
                              WIDTH - margin, 76, INK)

    # the source line at the bottom
    if source:
        src_font = _load_font(_find_font(_DEVANAGARI_FONTS) or _LATIN_FONT, 26)
        if src_font:
            draw.line([(margin, HEIGHT - 120), (WIDTH - margin, HEIGHT - 120)],
                      fill=RULE, width=2)
            draw.text((margin, HEIGHT - 96), f"स्रोत: {source}",
                      fill=MUTED, font=src_font)

    # the site mark, bottom-right
    mark_font = _load_font(_LATIN_FONT, 24)
    if mark_font:
        draw.text((WIDTH - margin - 190, HEIGHT - 96),
                  "localnews.developerbilaspur.in",
                  fill=(188, 180, 170), font=mark_font)

    image.save(out, "JPEG", quality=88, optimize=True)
    return out


def _draw_wrapped(draw, text: str, font, x: int, y: int, max_x: int,
                   line_h: int, fill) -> int:
    """Draw wrapped text; returns the new y."""
    words = text.split()
    line = ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) > (max_x - x) and line:
            draw.text((x, y), line, fill=fill, font=font)
            y += line_h
            line = word
        else:
            line = trial
    if line:
        draw.text((x, y), line, fill=fill, font=font)
        y += line_h
    return y


def alt_text_for(headline: str, source: str | None = None) -> str:
    """The alt text, in the site's own phrasing.

    The live site's images read
    `संपादकीय ग्राफिक: {headline} (फाइल फोटो: … / Wikimedia Commons, CC BY 3.0)`.
    Ours has no third-party photo, so the parenthetical is omitted -
    but the "संपादकीय ग्राफिक" prefix and the source credit are kept.
    """
    text = f"संपादकीय ग्राफिक: {headline}"
    if source:
        text += f" (स्रोत: {source})"
    return text
