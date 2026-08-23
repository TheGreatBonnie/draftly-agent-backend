"""CI/batch evaluation worker entrypoint (plan §9.2).

Runs the evaluation loop workflow once (batch mode) or on an interval
(CI watch mode), persisting reports to the evaluations store.

Usage:
    python -m workers.evaluation_worker [--watch]
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application
from draftly.observability.logging import configure_logging

INTERVAL_SECONDS = int(os.getenv("EVALUATION_INTERVAL_SECONDS", "3600"))


async def run(*, watch: bool = False) -> None:
    configure_logging(settings=get_settings())
    log = structlog.get_logger("draftly.worker.evaluation")
    structlog.contextvars.bind_contextvars(worker="evaluation")

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
        while True:
            result = await worker.run_task("evaluation.loop")
            log.info("evaluation_loop_result", result=result)
            if not watch:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
            except TimeoutError:
                pass
    finally:
        await application.shutdown()


def main() -> None:
    watch = "--watch" in sys.argv
    asyncio.run(run(watch=watch))


if __name__ == "__main__":
    main()
