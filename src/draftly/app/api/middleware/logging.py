import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("draftly.api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):

    async def dispatch(
        self,
        request: Request,
        call_next,
    ):
        request_id = str(uuid.uuid4())

        request.state.request_id = request_id

        started = time.perf_counter()

        try:
            response = await call_next(request)

            duration = time.perf_counter() - started

            response.headers[
                "X-Request-ID"
            ] = request_id

            logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_seconds": duration,
                },
            )

            return response

        except Exception:
            duration = time.perf_counter() - started

            logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_seconds": duration,
                },
            )

            raise
