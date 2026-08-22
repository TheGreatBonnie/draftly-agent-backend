from fastapi import Request
from starlette.middleware.base import (
    BaseHTTPMiddleware,
)
from starlette.responses import JSONResponse

from draftly.app.config import get_settings


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Protect internal Draftly API endpoints.

    This is separate from:
    - GitHub webhook verification
    - Slack signing verification
    - Discord request verification
    """

    async def dispatch(
        self,
        request: Request,
        call_next,
    ):
        settings = get_settings()

        supplied_key = request.headers.get("X-Draftly-API-Key")

        if supplied_key != settings.api_key:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Invalid API key.",
                },
            )

        return await call_next(request)
