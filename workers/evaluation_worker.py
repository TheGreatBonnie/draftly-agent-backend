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

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application

INTERVAL_SECONDS = int(os.getenv("EVALUATION_INTERVAL_SECONDS", "3600"))


async def run(*, watch: bool = False) -> None:
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
            print(f"evaluation.loop → {result}")  # noqa: T201 - CLI output
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
