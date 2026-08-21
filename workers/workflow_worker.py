"""Workflow resume/retry worker entrypoint (plan §9.2).

Boots the DraftlyApplication runtime and keeps the background worker
(scheduler + task runner) processing periodic jobs — including review
expiry and workflow retries — until interrupted.

Usage:
    python -m workers.workflow_worker
"""

from __future__ import annotations

import asyncio
import signal

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application


async def run() -> None:
    application = create_application(settings=get_settings())
    await application.startup()
    try:
        worker = application.worker
        if worker is None:
            raise RuntimeError("worker_enabled=false; nothing to run")
        await worker.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        await worker.stop()
    finally:
        await application.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
