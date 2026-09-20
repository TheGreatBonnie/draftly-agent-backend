"""Tavily ingestion/freshness integration (never the permanent vector DB).

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from draftly.integrations.tavily.errors import (
    RETRYABLE,
    TavilyError,
    TavilyErrorCode,
    is_retryable,
    retry_backoff,
)

if TYPE_CHECKING:
    from draftly.integrations.tavily.client import TavilyClient

__all__ = [
    "RETRYABLE",
    "TavilyClient",
    "TavilyError",
    "TavilyErrorCode",
    "is_retryable",
    "retry_backoff",
]


def __getattr__(name: str) -> Any:
    if name == "TavilyClient":
        from draftly.integrations.tavily.client import TavilyClient

        return TavilyClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
