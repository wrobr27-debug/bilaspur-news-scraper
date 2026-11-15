"""Explicit request budget (`ARCHITECTURE.md` D9).

Hard cap of outbound requests per verification attempt. Every outbound
request - redirects and retries included - must pass through `consume`.
"""

from __future__ import annotations

from ..errors import ScraperError
from ..schemas.enums import ErrorCode


class BudgetExhausted(ScraperError):
    def __init__(self, consumed: int, cap: int) -> None:
        super().__init__(
            f"request budget exhausted ({consumed}/{cap})", ErrorCode.E_BUDGET
        )
        self.consumed = consumed
        self.cap = cap


class RequestBudget:
    """Counts outbound requests against a hard cap."""

    def __init__(self, cap: int) -> None:
        if cap < 1:
            raise ValueError("budget cap must be >= 1")
        self.cap = cap
        self.consumed = 0

    @property
    def remaining(self) -> int:
        return self.cap - self.consumed

    def consume(self, n: int = 1) -> None:
        """Count n outbound requests; raise `BudgetExhausted` if over cap."""
        if n < 1:
            raise ValueError("must consume at least 1 request")
        if self.consumed + n > self.cap:
            self.consumed = self.cap
            raise BudgetExhausted(self.consumed, self.cap)
        self.consumed += n
