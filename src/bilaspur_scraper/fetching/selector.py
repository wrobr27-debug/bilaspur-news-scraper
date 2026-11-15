"""Fetcher selection - HTTP first, browser only when approved (D2, D10).

The browser is never a silent fallback: it is used only when the caller
explicitly asks for it, which happens only for a source whose registry
row carries `browser_approved = true` (operator approval, auditable).

D10 also says the global `BROWSER_CRAWLING_ENABLED` switch may only
*disable* browser crawling - it can never grant permission. It is
therefore an emergency kill-switch: when it is off, an approved source
falls back to the HTTP transport instead of launching Chromium.
"""

from __future__ import annotations

import logging

from ..config.settings import Settings
from .browser import BrowserFetcher
from .fetcher import FetchManager

logger = logging.getLogger(__name__)


def build_fetcher(settings: Settings, *, use_browser: bool = False):
    """Return the fetcher for this source.

    `use_browser=True` returns the Playwright adapter; otherwise the
    single HTTP transport (`FetchManager`) is used.

    When `BROWSER_CRAWLING_ENABLED` is off, the browser request is
    downgraded to HTTP (the global kill-switch, D10) and the reason is
    logged. The switch never enables a browser that was not approved.
    """
    if use_browser:
        if not settings.browser_crawling_enabled:
            logger.warning(
                "browser crawling is globally disabled "
                "(BROWSER_CRAWLING_ENABLED=false); falling back to HTTP")
            return FetchManager(settings)
        return BrowserFetcher(settings)
    return FetchManager(settings)
