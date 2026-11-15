"""Canonical error categorization + FetchManager behavior (offline; mocked
transport through a fake HttpxHttpClient)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx2
import pytest

from bilaspur_scraper.config.settings import Settings
from bilaspur_scraper.fetching.fetcher import FetchManager, categorize_exception, \
    categorize_status
from bilaspur_scraper.schemas.enums import ErrorCode


def _settings(**overrides) -> Settings:
    base = dict(
        database_url="sqlite:///:memory:", user_agent="test-agent",
        contact_email="", request_timeout_s=5, max_concurrency=1,
        per_domain_delay_s=0.0, max_retries=2, backoff_base_s=0.0,
        verification_request_budget=10, browser_crawling_enabled=False,
        respect_robots=True, dry_run=False, log_level="WARNING",
    )
    base.update(overrides)
    return Settings(**base)


class FakeResponse:
    def __init__(self, status_code, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def read(self):
        return self._body


def test_exception_categories():
    assert categorize_exception(asyncio.TimeoutError("t"))[0] == ErrorCode.E_TIMEOUT
    assert categorize_exception(httpx2.TimeoutException("t"))[0] == ErrorCode.E_TIMEOUT
    assert categorize_exception(httpx2.ConnectError("getaddrinfo failed"))[0] == \
        ErrorCode.E_DNS
    assert categorize_exception(
        httpx2.ConnectError("certificate verify failed"))[0] == ErrorCode.E_TLS
    assert categorize_exception(httpx2.ConnectError("refused"))[0] == \
        ErrorCode.E_CONNECT
    assert categorize_exception(httpx2.InvalidURL("bad"))[0] == ErrorCode.E_VALIDATION


def test_status_categories():
    assert categorize_status(429) == ErrorCode.E_RATE_LIMITED
    assert categorize_status(408) == ErrorCode.E_TIMEOUT
    assert categorize_status(404) == ErrorCode.E_HTTP_CLIENT
    assert categorize_status(503) == ErrorCode.E_HTTP_SERVER
    assert categorize_status(200) is None


def test_fetch_counts_redirect_chain_against_budget(monkeypatch):
    """Each redirect hop must consume budget (D9)."""
    fetcher = FetchManager(_settings())

    async def fake_send(url, headers=None):
        if url.endswith("/start"):
            return FakeResponse(301, {"location": "/hop-1"})
        return FakeResponse(200, {}, b"done")

    monkeypatch.setattr(fetcher._client, "send_request", fake_send)
    from bilaspur_scraper.fetching.budget import BudgetExhausted, RequestBudget
    budget = RequestBudget(3)  # start + 2 hops would be 3; only 2 hops allowed
    result = asyncio.run(fetcher.fetch(
        "https://example.org/start", budget=budget))
    assert result.ok
    assert budget.consumed == 2  # start + one hop (fetch stops at 200)
    assert result.redirect_chain == ["https://example.org/hop-1"]

    # Now a budget too small for the chain must exhaust.
    tight = RequestBudget(1)
    with pytest.raises(BudgetExhausted):
        asyncio.run(fetcher.fetch(
            "https://example.org/start", budget=tight))


def test_fetch_retries_transient_then_gives_up(monkeypatch):
    fetcher = FetchManager(_settings(max_retries=2))
    calls = {"n": 0}

    async def fake_send(url, headers=None):
        calls["n"] += 1
        raise httpx2.ConnectError("refused")

    monkeypatch.setattr(fetcher._client, "send_request", fake_send)
    result = asyncio.run(fetcher.fetch("https://example.org/x"))
    assert not result.ok
    assert result.error_code == ErrorCode.E_CONNECT
    assert calls["n"] == 2  # max_attempts honored


def test_fetch_does_not_retry_client_errors(monkeypatch):
    fetcher = FetchManager(_settings(max_retries=3))
    calls = {"n": 0}

    async def fake_send(url, headers=None):
        calls["n"] += 1
        return FakeResponse(404, {}, b"nope")

    monkeypatch.setattr(fetcher._client, "send_request", fake_send)
    result = asyncio.run(fetcher.fetch("https://example.org/x"))
    assert not result.ok
    assert result.error_code == ErrorCode.E_HTTP_CLIENT
    assert calls["n"] == 1  # 404 is terminal - no retries


def test_fetch_success_headers_and_text(monkeypatch):
    fetcher = FetchManager(_settings())
    body = "<html>ok</html>".encode()

    async def fake_send(url, headers=None):
        assert headers["User-Agent"] == "test-agent"
        return FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"}, body)

    monkeypatch.setattr(fetcher._client, "send_request", fake_send)
    result = asyncio.run(fetcher.fetch("https://example.org/x"))
    assert result.ok
    assert result.text() == "<html>ok</html>"
    assert result.header("content-type") == "text/html; charset=utf-8"
