"""Request budget (D9) - hard cap counting every counted request."""

from __future__ import annotations

import pytest

from bilaspur_scraper.fetching.budget import BudgetExhausted, RequestBudget


def test_budget_counts_requests():
    budget = RequestBudget(3)
    budget.consume()
    budget.consume()
    assert budget.remaining == 1
    budget.consume()
    assert budget.remaining == 0


def test_budget_raises_when_exhausted():
    budget = RequestBudget(2)
    budget.consume()
    budget.consume()
    assert budget.remaining == 0
    with pytest.raises(BudgetExhausted):
        budget.consume()
    assert budget.consumed == 2  # capped, never exceeds


def test_budget_multi_consume():
    budget = RequestBudget(5)
    budget.consume(3)
    with pytest.raises(BudgetExhausted):
        budget.consume(3)
    assert budget.consumed == 5


def test_budget_rejects_zero_cap():
    with pytest.raises(ValueError):
        RequestBudget(0)
    budget = RequestBudget(1)
    with pytest.raises(ValueError):
        budget.consume(0)
