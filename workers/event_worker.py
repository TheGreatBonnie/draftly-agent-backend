"""Event worker entrypoint (plan §9.2).

Runs the FastAPI app under uvicorn so webhook events are processed by
the in-process workflow runner. The DraftlyApplication lifespan owns
startup/shutdown.

Usage:
    python -m workers.event_worker
"""

from __future__ import annotations

import os

import structlog
import uvicorn

from draftly.app.api.app import create_api_app
from draftly.app.config import get_settings
from draftly.observability.logging import configure_logging


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    configure_logging(settings=get_settings())
    structlog.contextvars.bind_contextvars(worker="events")
    uvicorn.run(
        create_api_app(),
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=None,
    )


if __name__ == "__main__":
    main()
