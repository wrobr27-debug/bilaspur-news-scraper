"""FetchManager - polite, budgeted, single-authority HTTP fetching.

Built on Crawlee's `HttpxHttpClient` (`ARCHITECTURE.md` §14). Redirects
are followed manually so every hop is counted (D9). Retries are bounded
and counted. Raw bodies stay in memory (D13).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx2
from crawlee.http_clients import HttpxHttpClient

from ..config.settings import Settings
from ..schemas.enums import ErrorCode
from .budget import BudgetExhausted, RequestBudget

logger = logging.getLogger(__name__)

RETRYABLE_CODES = frozenset({
    ErrorCode.E_DNS,
    ErrorCode.E_CONNECT,
    ErrorCode.E_TIMEOUT,
    ErrorCode.E_HTTP_SERVER,
    ErrorCode.E_RATE_LIMITED,
})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5


@dataclass
class FetchResult:
    """Outcome of one fetch (`ARCHITECTURE.md` §7.2). Body is in-memory
    only and never persisted (D13)."""

    url: str
    final_url: str
    status_code: Optional[int]
    headers: dict[str, str]
    body: Optional[bytes]
    fetched_at: datetime
    duration_ms: int
    error_code: Optional[ErrorCode] = None
    error_message: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    cache_hit: bool = False
    used_browser: bool = False

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.status_code is not None \
            and 200 <= self.status_code < 300

    def header(self, name: str) -> Optional[str]:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def text(self) -> str:
        """Decode the body conservatively (charset header, else UTF-8)."""
        if not self.body:
            return ""
        charset = None
        content_type = self.header("content-type") or ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip('" ')
        try:
            return self.body.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def categorize_exception(exc: Exception) -> tuple[ErrorCode, str]:
    """Exactly one canonical category per failure (`ARCHITECTURE.md` §10).

    Crawlee's `HttpxHttpClient` raises `httpx2` exceptions (its vendored
    stack): timeouts surface as `asyncio.TimeoutError`, transport failures
    as `httpx2.TransportError` subclasses.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return ErrorCode.E_TIMEOUT, f"timeout: {exc}"
    if isinstance(exc, httpx2.TimeoutException):
        return ErrorCode.E_TIMEOUT, f"timeout: {exc}"
    if isinstance(exc, httpx2.ConnectError):
        cause = str(getattr(exc, "__cause__", "") or exc)
        lowered = cause.lower()
        if "certificate" in lowered or "ssl" in lowered or "verify" in lowered:
            return ErrorCode.E_TLS, f"TLS failure: {exc}"
        if "getaddrinfo" in lowered or "name or service" in lowered \
                or "nodename" in lowered:
            return ErrorCode.E_DNS, f"name resolution failure: {exc}"
        return ErrorCode.E_CONNECT, f"connection failure: {exc}"
    if isinstance(exc, httpx2.TransportError):
        return ErrorCode.E_CONNECT, f"transport failure: {exc}"
    if isinstance(exc, httpx2.InvalidURL):
        return ErrorCode.E_VALIDATION, f"invalid URL: {exc}"
    if isinstance(exc, httpx2.HTTPError):
        return ErrorCode.E_CONNECT, f"HTTP failure: {exc}"
    return ErrorCode.E_CONNECT, f"unexpected fetch error: {exc}"


async def polite_wait(last_request_at: dict[str, float], host_delay_s: float,
                      url: str) -> None:
    """Shared per-host politeness delay (HTTP and browser paths)."""
    from urllib.parse import urlsplit

    host = urlsplit(url).netloc
    last = last_request_at.get(host)
    if last is not None:
        wait = host_delay_s - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)
    last_request_at[host] = time.monotonic()


def categorize_status(status: int) -> Optional[ErrorCode]:
    """Canonical category for a completed response status."""
    if status == 429:
        return ErrorCode.E_RATE_LIMITED
    if status == 408:
        return ErrorCode.E_TIMEOUT
    if 400 <= status < 500:
        return ErrorCode.E_HTTP_CLIENT
    if 500 <= status < 600:
        return ErrorCode.E_HTTP_SERVER
    return None


class FetchManager:
    """The only component that issues HTTP requests."""

    def __init__(self, settings: Settings, *, host_delay_s: float | None = None) -> None:
        self._settings = settings
        self._host_delay_s = (
            settings.per_domain_delay_s if host_delay_s is None else host_delay_s
        )
        timeout = httpx2.Timeout(settings.request_timeout_s)
        self._client = HttpxHttpClient(
            persist_cookies_per_session=False,
            http2=True,
            follow_redirects=False,   # redirects are followed manually + counted
            timeout=timeout,
        )
        self._last_request_at: dict[str, float] = {}

    async def aclose(self) -> None:
        await self._client.cleanup()

    @property
    def user_agent(self) -> str:
        return self._settings.user_agent

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self._settings.user_agent,
            "Accept": "text/html, application/xhtml+xml, application/xml;q=0.9, "
                      "application/rss+xml, application/atom+xml;q=0.9, */*;q=0.8",
            "Accept-Language": "en-IN, hi-IN, en;q=0.8, hi;q=0.7",
        }
        return headers

    async def _polite_wait(self, url: str) -> None:
        await polite_wait(self._last_request_at, self._host_delay_s, url)

    async def fetch(
        self,
        url: str,
        *,
        budget: RequestBudget | None = None,
        headers: dict[str, str] | None = None,
        max_attempts: int | None = None,
        polite: bool = True,
    ) -> FetchResult:
        """Fetch one URL. Redirects and retries are explicit and counted."""
        if max_attempts is None:
            max_attempts = max(1, self._settings.max_retries)
        started = time.monotonic()
        chain: list[str] = []
        current_url = url
        attempt = 0
        last_error: Optional[tuple[ErrorCode, str]] = None
        last_status: Optional[int] = None
        last_headers: dict[str, str] = {}
        retry_after_s: float | None = None

        while True:
            if polite:
                await self._polite_wait(current_url)
            if budget is not None:
                budget.consume()  # raises BudgetExhausted (D9: counted)
            fetched_at = datetime.now(timezone.utc)
            try:
                response = await self._client.send_request(
                    current_url, headers={**self._default_headers(), **(headers or {})}
                )
                status = response.status_code
                resp_headers = {str(k): str(v) for k, v in dict(response.headers).items()}
                last_status = status
                last_headers = resp_headers
                if status in REDIRECT_STATUSES:
                    location = resp_headers.get("location")
                    if not location:
                        return self._finish(current_url, current_url, status,
                                            resp_headers, b"", fetched_at, started,
                                            chain, ErrorCode.E_HTTP_CLIENT,
                                            "redirect without Location header")
                    from urllib.parse import urljoin

                    next_url = urljoin(current_url, location)
                    chain.append(next_url)
                    if len(chain) > MAX_REDIRECTS:
                        return self._finish(current_url, current_url, status,
                                            resp_headers, b"", fetched_at, started,
                                            chain, ErrorCode.E_HTTP_CLIENT,
                                            "too many redirects")
                    current_url = next_url
                    continue  # a redirect hop is another counted request
                body = await response.read()
                if status < 300:
                    return self._finish(url, current_url, status, resp_headers,
                                        body, fetched_at, started, chain, None, "")
                error_code = categorize_status(status)
                if status == 429:
                    ra = resp_headers.get("retry-after")
                    if ra and ra.strip().isdigit():
                        retry_after_s = float(ra)
                last_error = (error_code, f"HTTP {status}")
            except BudgetExhausted:
                raise
            except Exception as exc:  # noqa: BLE001 - single categorization point
                code, message = categorize_exception(exc)
                last_error = (code, message)
                body = b""
                resp_headers = {}

            attempt += 1
            transient = last_error is not None and last_error[0] in RETRYABLE_CODES
            if not transient or attempt >= max_attempts:
                code, message = last_error or (ErrorCode.E_HTTP_CLIENT, "fetch failed")
                return self._finish(url, current_url, last_status,
                                    last_headers, b"", fetched_at, started,
                                    chain, code, message)
            delay = self._settings.backoff_base_s * (2 ** (attempt - 1))
            delay += random.uniform(0, delay / 2)
            if retry_after_s is not None:
                delay = max(delay, retry_after_s)
                retry_after_s = None
            logger.info("retrying %s (attempt %d/%d) after %.1fs (%s)",
                        current_url, attempt + 1, max_attempts, delay,
                        last_error[0].value if last_error else "?")
            await asyncio.sleep(delay)

    def _finish(self, url: str, final_url: str, status: Optional[int],
                headers: dict[str, str], body: bytes, fetched_at: datetime,
                started: float, chain: list[str], error_code: Optional[ErrorCode],
                error_message: str) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=status,
            headers=headers,
            body=body,
            fetched_at=fetched_at,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=error_code,
            error_message=error_message,
            redirect_chain=chain,
        )
