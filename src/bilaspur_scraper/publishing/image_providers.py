"""Featured image generation - pluggable providers.

The site needs a 1200x675 editorial graphic for every post. Three
providers are tried in order, and the first that works is used:

  1. **`local`** - a typographic card rendered with Pillow. Always
     available, needs no key and no network, and it is the only option
     that is unambiguously rights-clean: it publishes our own headline
     and our own facts, and nobody else's photograph.

  2. **`minimax`** - MiniMax's own image API (`image-01`), reached
     with a MiniMax API secret key. Needs `MINIMAX_API_KEY`.

  3. **`nvidia`** - NVIDIA NIM's `/v1/images/generations`, reached
     with the NVIDIA API key. Only works if the account has an image
     model enabled; on the current account it answers 404 for every
     image model, so this provider reports itself unavailable rather
     than failing the run.

Whichever provider is used, the result is a 1200x675 JPEG named for the
post, matching the site's own naming convention.

The default is `local`. That is a deliberate choice, not a fallback:
the site's images credit a photographer and a licence
(`संपादकीय ग्राफिक: … (फाइल फोटो: … / Wikimedia Commons, CC BY 3.0)`),
so a generated card reproduces the design intent without putting a
third party's photograph under our masthead.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WIDTH = 1200
HEIGHT = 675

PROVIDER_ORDER = ("local", "minimax", "nvidia")


def available_providers() -> list[str]:
    """Which providers are configured right now."""
    out = []
    if os.environ.get("MINIMAX_API_KEY"):
        out.append("minimax")
    if os.environ.get("NVIDIA_API_KEY"):
        out.append("nvidia")
    out.insert(0, "local")   # local always works
    return out


def _generate_local(image_path: Path, headline: str, channel: str | None,
                    source: str | None, published_label: str | None) -> Path:
    """The typographic card. Delegates to the shared renderer."""
    from .featured_image import render_featured_image
    return render_featured_image(
        headline=headline, channel=channel, source=source,
        published_label=published_label, output_path=image_path)


def _generate_minimax(image_path: Path, headline: str,
                       channel: str | None) -> Optional[Path]:
    """MiniMax `image-01` through its own API."""
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        return None
    body = json.dumps({
        "model": os.environ.get("MINIMAX_IMAGE_MODEL", "image-01"),
        "prompt": (f"A clean editorial news graphic for a Hindi local news "
                   f"website. Soft off-white background, deep red accent "
                   f"band at the top, bold dark Devanagari headline text: "
                   f"\"{headline}\". No photographs, no people, no logos, "
                   f"pure typography, generous margins, 16:9."),
        "aspect_ratio": "16:9",
        "response_format": "url",
    }).encode("utf-8")
    for base in ("https://api.minimax.chat", "https://api.minimax.io"):
        req = urllib.request.Request(
            f"{base}/v1/image_generation", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.info("minimax image generation failed at %s: %s",
                        base, exc)
            continue
        payload = data.get("data") or {}
        urls = payload.get("image_urls") or payload.get("url") or []
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            logger.info("minimax returned no image: %s",
                        str(data.get("base_resp"))[:120])
            continue
        try:
            with urllib.request.urlopen(urls[0], timeout=120) as r:
                raw = r.read()
        except Exception as exc:  # noqa: BLE001
            logger.info("could not download the generated image: %s", exc)
            continue
        image_path.write_bytes(raw)
        return image_path
    return None


def _generate_nvidia(image_path: Path, headline: str,
                      channel: str | None) -> Optional[Path]:
    """NVIDIA NIM image generation, if the account has a model enabled."""
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        return None
    model = os.environ.get("NVIDIA_IMAGE_MODEL", "")
    if not model:
        # No image model configured: the account cannot generate images.
        return None
    body = json.dumps({
        "model": model,
        "prompt": (f"Editorial news card, off-white background, deep red "
                   f"accent bar, bold Devanagari headline: {headline}. "
                   f"Typography only, no photograph."),
        "n": 1, "size": "1024x1024",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/images/generations",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("nvidia image generation failed: %s", exc)
        return None
    items = data.get("data") or []
    if not items:
        return None
    item = items[0]
    if item.get("b64_json"):
        image_path.write_bytes(base64.b64decode(item["b64_json"]))
        return image_path
    if item.get("url"):
        try:
            with urllib.request.urlopen(item["url"], timeout=120) as r:
                image_path.write_bytes(r.read())
            return image_path
        except Exception as exc:  # noqa: BLE001
            logger.info("could not download the generated image: %s", exc)
    return None


def generate_featured_image(image_path: Path, *, headline: str,
                            channel: str | None = None,
                            source: str | None = None,
                            published_label: str | None = None,
                            provider: str = "auto") -> tuple[Path, str]:
    """Generate the featured image. Returns `(path, provider_used)`.

    `provider="auto"` tries each configured provider in order; an
    explicit name tries only that one.
    """
    image_path.parent.mkdir(parents=True, exist_ok=True)
    order = PROVIDER_ORDER if provider == "auto" else (provider,)

    for name in order:
        try:
            if name == "local":
                return _generate_local(image_path, headline, channel, source,
                                       published_label), "local"
            if name == "minimax":
                got = _generate_minimax(image_path, headline, channel)
                if got:
                    return got, "minimax"
            if name == "nvidia":
                got = _generate_nvidia(image_path, headline, channel)
                if got:
                    return got, "nvidia"
        except Exception as exc:  # noqa: BLE001
            logger.warning("image provider %s failed: %s", name, exc)
    raise RuntimeError(
        f"no image provider succeeded (tried: {', '.join(order)})")


def alt_text_for(headline: str, source: str | None = None) -> str:
    """The alt text, in the site's own phrasing."""
    from .featured_image import alt_text_for as _alt
    return _alt(headline, source)
