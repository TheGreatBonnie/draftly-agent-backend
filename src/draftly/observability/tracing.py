"""Request correlation and lightweight tracing (plan §9.3).

A contextvar carries the correlation id across a request/workflow;
``traced`` records span durations into the shared metrics registry.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    """Start a fresh correlation scope and return its id."""
    correlation_id = uuid.uuid4().hex
    _correlation_id.set(correlation_id)
    return correlation_id


def current_correlation_id() -> str:
    """The active correlation id ('' outside any scope)."""
    return _correlation_id.get()


def bind_correlation_id(correlation_id: str) -> None:
    """Adopt an externally supplied correlation id (e.g. from headers)."""
    _correlation_id.set(correlation_id)


@asynccontextmanager
async def traced(
    name: str,
    metrics: Any = None,
    attributes: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Time a span; records `<name>.duration_ms` when metrics given."""
    import time

    span: dict[str, Any] = {
        "name": name,
        "correlation_id": current_correlation_id(),
        "attributes": attributes or {},
    }
    start = time.perf_counter()
    try:
        yield span
        span["status"] = "ok"
    except Exception:
        span["status"] = "error"
        if metrics is not None:
            metrics.increment(f"{name}.errors")
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        span["duration_ms"] = round(duration_ms, 3)
        if metrics is not None:
            metrics.increment(f"{name}.calls")
            metrics.observe(f"{name}.duration_ms", duration_ms / 1000.0)
