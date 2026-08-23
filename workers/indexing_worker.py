"""Documentation indexing worker entrypoint (plan §9.2).

Runs the documentation sync workflow on an interval — pulling repo
content into the documentation store and refreshing the index.

Usage:
    python -m workers.indexing_worker
"""

from __future__ import annotations

import asyncio
import os
import signal

import structlog

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application
from draftly.observability.logging import configure_logging

INTERVAL_SECONDS = int(os.getenv("INDEXING_INTERVAL_SECONDS", "300"))


async def run() -> None:
    configure_logging(settings=get_settings())
    log = structlog.get_logger("draftly.worker.indexing")
    structlog.contextvars.bind_contextvars(worker="indexing")

    application = create_application(settings=get_settings())
    await application.startup()
    try:
        worker = application.worker
        if worker is None:
            raise RuntimeError("worker_enabled=false; nothing to run")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        while not stop.is_set():
            try:
                await worker.run_task("documentation.sync")
            except Exception:
                log.exception("indexing_run_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
            except TimeoutError:
                pass
    finally:
        await application.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
