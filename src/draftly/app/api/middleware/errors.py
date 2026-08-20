import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("draftly.errors")


def register_error_handlers(
    application: FastAPI,
) -> None:

    @application.exception_handler(Exception)
    async def unhandled_exception(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:

        request_id = getattr(
            request.state,
            "request_id",
            None,
        )

        logger.exception(
            "Unhandled application exception.",
            extra={
                "request_id": request_id,
                "path": request.url.path,
            },
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": "An unexpected error occurred.",
                "request_id": request_id,
            },
        )
