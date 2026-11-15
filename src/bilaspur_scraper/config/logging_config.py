"""Structured logging with stable error codes (`SKILLS.md` §14).

Formatter emits `LEVEL code=... logger=... message`. No credentials,
cookies, tokens, or private data are ever logged.
"""

from __future__ import annotations

import logging


class _StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        code = getattr(record, "error_code", "-")
        base = super().format(record)
        return f"{base} code={code}"


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_StructuredFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
