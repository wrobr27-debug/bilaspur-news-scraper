"""Environment-driven configuration (`PLAN.md` §12, `ARCHITECTURE.md` §6.1).

All values come from environment variables with safe defaults. The
contact email lives only in the local, untracked `.env` - never in code
or tracked files. Secrets are never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError


def load_env_file(path: Path | None = None) -> None:
    """Load the local, untracked `.env` (cwd first, then project root).

    Existing environment variables always win; the file never overrides
    an explicit shell setting.
    """
    candidates = [path] if path else [
        Path(".env"),
        Path(__file__).resolve().parents[3] / ".env",
    ]
    for candidate in candidates:
        if not candidate or not candidate.is_file():
            continue
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    database_url: str
    user_agent: str
    contact_email: str
    request_timeout_s: int
    max_concurrency: int
    per_domain_delay_s: float
    max_retries: int
    backoff_base_s: float
    verification_request_budget: int
    browser_crawling_enabled: bool
    respect_robots: bool
    dry_run: bool
    log_level: str


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    return int(raw) if raw.strip() else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    return float(raw) if raw.strip() else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    load_env_file()
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///data/news.db"),
        user_agent=os.environ.get("USER_AGENT", ""),
        contact_email=os.environ.get("CONTACT_EMAIL", ""),
        request_timeout_s=_env_int("REQUEST_TIMEOUT_S", 20),
        max_concurrency=_env_int("MAX_CONCURRENCY", 4),
        per_domain_delay_s=_env_float("PER_DOMAIN_DELAY_S", 2.0),
        max_retries=_env_int("MAX_RETRIES", 3),
        backoff_base_s=_env_float("BACKOFF_BASE_S", 1.0),
        verification_request_budget=_env_int("VERIFICATION_REQUEST_BUDGET", 10),
        browser_crawling_enabled=_env_bool("BROWSER_CRAWLING_ENABLED", True),
        respect_robots=_env_bool("RESPECT_ROBOTS", True),
        dry_run=_env_bool("DRY_RUN", False),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )


def validate_for_live_crawl(settings: Settings) -> None:
    """Live crawls require a descriptive, contactable user agent.

    Dry-run, registry import, and verification CLI scaffolding may proceed
    without contact configuration (`ARCHITECTURE.md` §6.1).
    """
    missing = [name for name, value in (
        ("USER_AGENT", settings.user_agent),
        ("CONTACT_EMAIL", settings.contact_email),
    ) if not value.strip()]
    if missing:
        raise ConfigError(
            "live crawl requires contact configuration; missing: "
            + ", ".join(missing)
            + " (configure them in the local, untracked .env)"
        )
