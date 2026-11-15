"""Application error types carrying a canonical error category."""

from __future__ import annotations

from .schemas.enums import ErrorCode


class ScraperError(Exception):
    """Base error; every failure carries exactly one canonical category."""

    def __init__(self, message: str, code: ErrorCode = ErrorCode.E_CONFIG) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigError(ScraperError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.E_CONFIG)


class LifecycleError(ScraperError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.E_VALIDATION)


class QuarantineError(ScraperError):
    """A record failed validation and is quarantined, not silently dropped."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.E_VALIDATION)
