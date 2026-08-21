"""Event worker entrypoint (plan §9.2).

Runs the FastAPI app under uvicorn so webhook events are processed by
the in-process workflow runner. The DraftlyApplication lifespan owns
startup/shutdown.

Usage:
    python -m workers.event_worker
"""

from __future__ import annotations

import os

import uvicorn

from draftly.app.api.app import create_app


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
