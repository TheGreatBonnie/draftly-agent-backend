# app/workers/worker.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class DraftlyWorker:
    """
    Draftly background worker runtime.

    The worker coordinates the task runner and scheduler.
    """

    task_runner: Any
    scheduler: Any

    _started: bool = False

    async def start(self) -> None:
        """
        Start background processing.
        """

        if self._started:
            return

        await self.scheduler.start()

        self._started = True

        logger.info(
            "Draftly worker started",
        )

    async def stop(self) -> None:
        """
        Stop background processing gracefully.
        """

        if not self._started:
            return

        await self.scheduler.stop()

        self._started = False

        logger.info(
            "Draftly worker stopped",
        )

    async def run_task(
        self,
        name: str,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a task immediately.
        """

        return await self.task_runner.run(
            name,
            **kwargs,
        )
