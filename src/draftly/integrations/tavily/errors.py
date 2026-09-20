"""Tavily error taxonomy and retry policy.

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md (Error model).
Only ``rate_limit`` | ``timeout`` | ``upstream`` are retryable, with bounded
exponential backoff + jitter honoring server guidance.
"""

from __future__ import annotations

import random
from enum import StrEnum


class TavilyErrorCode(StrEnum):
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_URL = "unsupported_url"
    RATE_LIMIT = "rate_limit"
    CREDIT_LIMIT = "credit_limit"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    INVALID_RESPONSE = "invalid_response"


RETRYABLE = frozenset(
    {TavilyErrorCode.RATE_LIMIT, TavilyErrorCode.TIMEOUT, TavilyErrorCode.UPSTREAM}
)


class TavilyError(RuntimeError):
    """Normalized Tavily failure; ``code`` is always a TavilyErrorCode."""

    def __init__(
        self,
        code: TavilyErrorCode,
        message: str,
        *,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.code = code
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


def is_retryable(code: TavilyErrorCode) -> bool:
    return code in RETRYABLE


def retry_backoff(
    attempt: int, *, base: float = 1.0, cap: float = 20.0, jitter: float = 0.2
) -> float:
    """Exponential backoff with bounded jitter; attempt is 0-indexed."""
    delay = min(base * (2**attempt), cap)
    return delay * (1.0 + random.uniform(-jitter, jitter))
