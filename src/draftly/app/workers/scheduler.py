# app/workers/scheduler.py

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class DraftlyScheduler:
    """
    Runtime adapter for Draftly's scheduler.

    The actual scheduling definitions live in:

        scheduler/

    This module only manages execution of those scheduled jobs.
    """

    def __init__(
        self,
        *,
        registry: Any,
        task_runner: Any,
        interval_seconds: int = 30,
    ) -> None:
        self.registry = registry
        self.task_runner = task_runner
        self.interval_seconds = interval_seconds

        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """
        Start the scheduler loop.
        """

        if self._running:
            return

        self._running = True

        self._task = asyncio.create_task(
            self._run_loop(),
            name="draftly-scheduler",
        )

        logger.info("Draftly scheduler started")

    async def stop(self) -> None:
        """
        Stop the scheduler gracefully.
        """

        if not self._running:
            return

        self._running = False

        if self._task is not None:
            self._task.cancel()

            try:
                await self._task
            except asyncio.CancelledError:
                pass

            self._task = None

        logger.info("Draftly scheduler stopped")

    async def _run_loop(self) -> None:
        """
        Poll the job registry and execute due jobs.
        """

        while self._running:
            try:
                jobs = self.registry.get_due_jobs()

                for job in jobs:
                    await self._execute_job(job)

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Scheduler loop failed",
                )

            await asyncio.sleep(
                self.interval_seconds,
            )

    async def _execute_job(
        self,
        job: Any,
    ) -> None:
        """
        Execute a scheduled job through the task runner.
        """

        logger.info(
            "Executing scheduled job",
            extra={
                "job": getattr(
                    job,
                    "name",
                    "unknown",
                ),
            },
        )

        await self.task_runner.run(
            job.name,
            **getattr(
                job,
                "arguments",
                {},
            ),
        )
