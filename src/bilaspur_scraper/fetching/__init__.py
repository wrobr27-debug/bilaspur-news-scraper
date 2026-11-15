"""Fetching layer (Phase 1) - one HTTP transport, one browser adapter.

Design (`ARCHITECTURE.md` §6.7, D8, D9, D10, D16):
- one HTTP transport: Crawlee's `HttpxHttpClient` (the same stack its
  `HttpCrawler` uses). No second HTTP library.
- ONE retry authority: the retry policy lives only in `FetchManager`
  (and its browser twin `BrowserFetcher`). The transport performs no
  implicit retries.
- every outbound request - including redirect hops and retries - is
  counted against an explicit `RequestBudget` when one is provided.
- one canonical error category per failure (`ErrorCode`).
- raw response bodies stay in memory and are never persisted (D13).
- the browser adapter (`BrowserFetcher`) exists only for sources the
  operator approved (`sources.browser_approved`, D10) and returns the
  same `FetchResult` contract, so nothing downstream changes.
"""

from .budget import BudgetExhausted, RequestBudget
from .browser import BrowserFetcher
from .fetcher import FetchManager, FetchResult
from .robots import RobotsDecision, RobotsGate
from .selector import build_fetcher

__all__ = [
    "BudgetExhausted",
    "BrowserFetcher",
    "FetchManager",
    "FetchResult",
    "RequestBudget",
    "RobotsDecision",
    "RobotsGate",
    "build_fetcher",
]
