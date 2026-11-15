"""robots.txt gate (`SKILLS.md` §5.3, `ARCHITECTURE.md` §10).

Parsing uses `protego` (installed with Crawlee). The robots fetch itself
counts against the request budget when one is supplied (D9). A 404 means
no robots file exists (standard: no restrictions). A transport/5xx
failure is `E_ROBOTS_UNAVAILABLE` and is treated conservatively.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

import protego

from .fetcher import FetchManager, FetchResult

logger = logging.getLogger(__name__)


@dataclass
class RobotsDecision:
    url: str
    allowed: bool
    crawl_delay_s: float | None
    unavailable: bool
    absent: bool = False
    status_code: int | None = None
    error_code: str | None = None   # 'E_POLICY' | 'E_ROBOTS_UNAVAILABLE' | None
    sitemaps: list[str] | None = None

    @property
    def explicitly_disallowed(self) -> bool:
        return not self.unavailable and not self.absent and not self.allowed


class RobotsGate:
    def __init__(self, fetcher: FetchManager) -> None:
        self._fetcher = fetcher

    async def check(self, url: str, *, budget=None) -> RobotsDecision:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        result = await self._fetcher.fetch(robots_url, budget=budget)
        if not result.ok:
            if result.status_code == 404 or result.status_code == 410:
                logger.info("robots.txt absent for %s (HTTP %s)", parts.netloc,
                            result.status_code)
                return RobotsDecision(
                    url=robots_url, allowed=True, crawl_delay_s=None,
                    unavailable=False, absent=True,
                    status_code=result.status_code,
                )
            return RobotsDecision(
                url=robots_url, allowed=False, crawl_delay_s=None,
                unavailable=True, status_code=result.status_code,
                error_code="E_ROBOTS_UNAVAILABLE",
            )
        text = result.text()
        rules = protego.Protego.parse(text)
        ua = self._fetcher.user_agent
        product = ua.split("/")[0].strip() if ua else "*"
        allowed = rules.can_fetch(product, url) or rules.can_fetch("*", url)
        delay = rules.crawl_delay(product) or rules.crawl_delay("*")
        sitemaps = [str(s) for s in rules.sitemaps] or None
        decision = RobotsDecision(
            url=robots_url,
            allowed=bool(allowed),
            crawl_delay_s=float(delay) if delay else None,
            unavailable=False,
            status_code=result.status_code,
            sitemaps=sitemaps,
        )
        if not decision.allowed:
            decision.error_code = "E_POLICY"
        return decision
