"""Request logging + correlation-id binding middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from draftly.observability.tracing import (
    bind_correlation_id,
    clear_correlation_id,
    new_correlation_id,
)

logger = structlog.get_logger("draftly.api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        inbound = request.headers.get("X-Request-ID")
        request_id = inbound or new_correlation_id()

        bind_correlation_id(request_id)
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)

            duration = time.perf_counter() - started

            response.headers["X-Request-ID"] = request_id

            logger.info(
                "request_completed",
                status_code=response.status_code,
                duration_seconds=round(duration, 6),
            )

            return response

        except Exception:
            duration = time.perf_counter() - started

            logger.exception(
                "request_failed",
                duration_seconds=round(duration, 6),
            )

            raise

        finally:
            structlog.contextvars.unbind_contextvars(
                "request_id",
                "method",
                "path",
            )
            clear_correlation_id()
