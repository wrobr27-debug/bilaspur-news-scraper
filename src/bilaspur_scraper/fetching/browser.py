"""Browser fetcher (Playwright/Chromium) - `ARCHITECTURE.md` D10.

The browser is a **second adapter behind the same contract** as
`FetchManager`: it returns a `FetchResult` (status, headers, body bytes),
so robots gating, discovery, extraction, relevance, dedup and storage all
work unchanged. It exists only for sources the operator explicitly
approved (`sources.browser_approved`).

Rules enforced here:
- one navigation = one counted request (D9 budget accounting);
- heavy subresources (images/media/fonts) are aborted, so a page load
  costs the target server a document request, not dozens;
- concurrency 1 (D10): one browser, one page, serialized by a lock;
- polite per-host delay and bounded retries, identical to the HTTP path;
- the honest contact user agent is used - the browser does not disguise
  itself as a human reader.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

from ..config.settings import Settings
from ..errors import ConfigError
from ..schemas.enums import ErrorCode
from .budget import BudgetExhausted
from .fetcher import (
    RETRYABLE_CODES,
    FetchResult,
    categorize_status,
    polite_wait,
)

logger = logging.getLogger(__name__)

BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})


def categorize_browser_exception(exc: Exception) -> tuple[ErrorCode, str]:
    """One canonical category per browser failure (`ARCHITECTURE.md` §10)."""
    text = str(exc)
    lowered = text.lower()
    if "timeout" in lowered or "exceeded" in lowered:
        return ErrorCode.E_TIMEOUT, f"browser timeout: {text}"
    if "err_name_not_resolved" in lowered or "err_name_resolution" in lowered:
        return ErrorCode.E_DNS, f"name resolution failure: {text}"
    if "err_cert" in lowered or "ssl" in lowered or "certificate" in lowered:
        return ErrorCode.E_TLS, f"TLS failure: {text}"
    if "err_connection" in lowered or "err_address_unreachable" in lowered:
        return ErrorCode.E_CONNECT, f"connection failure: {text}"
    if "net::" in lowered:
        return ErrorCode.E_CONNECT, f"network failure: {text}"
    return ErrorCode.E_CONNECT, f"browser failure: {text}"


class BrowserFetcher:
    """Chromium-backed fetcher with the `FetchManager` contract."""

    def __init__(self, settings: Settings, *, headless: bool = True,
                 render_wait_ms: int = 400,
                 block_resources: bool = True) -> None:
        if not settings.user_agent.strip():
            raise ConfigError(
                "browser mode requires an identifying USER_AGENT with a "
                "contact address (configure it in the local, untracked .env)")
        self._settings = settings
        self._host_delay_s = settings.per_domain_delay_s
        self._last_request_at: dict[str, float] = {}
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self._headless = headless
        self._render_wait_ms = render_wait_ms
        self._block_resources = block_resources

    @property
    def user_agent(self) -> str:
        return self._settings.user_agent

    async def _ensure_started(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on env
            raise ConfigError(
                "browser mode requires Playwright: "
                "pip install playwright && python -m playwright install chromium"
            ) from exc
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless)
            self._context = await self._browser.new_context(
                user_agent=self._settings.user_agent,
                locale="en-IN",
                viewport={"width": 1366, "height": 900},
                extra_http_headers={
                    "Accept-Language": "en-IN, hi-IN, en;q=0.8, hi;q=0.7",
                },
            )
            self._page = await self._context.new_page()
            if self._block_resources:
                await self._page.route("**/*", self._route)
        except Exception as exc:  # noqa: BLE001 - one clear config error
            await self._close_browser()
            raise ConfigError(f"could not start Chromium: {exc}") from exc

    async def _route(self, route) -> None:
        """Abort heavy subresources; keep document/script/xhr intact."""
        try:
            resource_type = route.request.resource_type
        except Exception:  # noqa: BLE001 - defensive
            resource_type = "document"
        if resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()

    async def fetch(self, url: str, *, budget=None, headers: dict | None = None,
                    max_attempts: int | None = None,
                    polite: bool = True) -> FetchResult:
        """Render one URL in Chromium and return it as a `FetchResult`."""
        if max_attempts is None:
            max_attempts = max(1, self._settings.max_retries)
        started = time.monotonic()
        attempt = 0
        last_error: Optional[tuple[ErrorCode, str]] = None

        async with self._lock:  # D10: concurrency 1
            await self._ensure_started()
            while True:
                if polite:
                    await polite_wait(self._last_request_at, self._host_delay_s,
                                      url)
                if budget is not None:
                    budget.consume()  # one navigation = one counted request
                try:
                    response = await self._page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self._settings.request_timeout_s * 1000,
                    )
                    if response is None:
                        html = await self._page.content()
                        return self._finish(url, self._page.url, None, {},
                                            html.encode("utf-8"), started)
                    status = response.status
                    resp_headers = {str(k): str(v) for k, v in
                                    (await response.all_headers()).items()}
                    await self._settle()
                    html = await self._page.content()
                    body = html.encode("utf-8")
                    if status < 300:
                        return self._finish(url, self._page.url, status,
                                            resp_headers, body, started)
                    last_error = (categorize_status(status), f"HTTP {status}")
                except BudgetExhausted:
                    raise
                except Exception as exc:  # noqa: BLE001 - one categorization point
                    last_error = categorize_browser_exception(exc)

                attempt += 1
                transient = (last_error is not None
                             and last_error[0] in RETRYABLE_CODES)
                if not transient or attempt >= max_attempts:
                    code, message = last_error or (
                        ErrorCode.E_HTTP_CLIENT, "browser fetch failed")
                    return self._finish(url, url, None, {}, b"", started,
                                        error_code=code, error_message=message)
                delay = self._settings.backoff_base_s * (2 ** (attempt - 1))
                delay += random.uniform(0, delay / 2)
                logger.info("retrying %s in browser (attempt %d/%d) after %.1fs "
                            "(%s)", url, attempt + 1, max_attempts, delay,
                            last_error[0].value if last_error else "?")
                await asyncio.sleep(delay)

    async def _settle(self) -> None:
        """Wait for JS to finish injecting content (best effort)."""
        try:
            await self._page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:  # noqa: BLE001 - settle is best effort
            pass
        if self._render_wait_ms:
            await asyncio.sleep(self._render_wait_ms / 1000)

    def _finish(self, url: str, final_url: str, status: Optional[int],
                headers: dict[str, str], body: bytes, started: float,
                error_code: Optional[ErrorCode] = None,
                error_message: str = "") -> FetchResult:
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=status,
            headers=headers,
            body=body,
            fetched_at=datetime.now(timezone.utc),
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=error_code,
            error_message=error_message,
            used_browser=True,
        )

    async def _close_browser(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass
        self._context = None
        self._browser = None
        self._page = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None

    async def aclose(self) -> None:
        await self._close_browser()
