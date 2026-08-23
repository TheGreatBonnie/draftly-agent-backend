# app/workers/task_runner.py

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


TaskHandler = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class Task:
    """
    A unit of work executed by the Draftly worker.
    """

    name: str
    handler: TaskHandler


class TaskRunner:
    """
    Executes registered asynchronous tasks.

    The runner is intentionally generic. It does not know how
    documentation, support, evaluation, or GitHub workflows
    work.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskHandler] = {}

    def register(
        self,
        name: str,
        handler: TaskHandler,
    ) -> None:
        """
        Register a task handler.
        """

        if name in self._tasks:
            raise ValueError(
                f"Task already registered: {name}",
            )

        self._tasks[name] = handler

    def has_task(
        self,
        name: str,
    ) -> bool:
        return name in self._tasks

    async def run(
        self,
        name: str,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a registered task.
        """

        handler = self._tasks.get(name)

        if handler is None:
            raise ValueError(
                f"Unknown Draftly task: {name}",
            )

        logger.info(
            "Running Draftly task",
            task=name,
        )

        try:
            result = await handler(**kwargs)

        except Exception:
            logger.exception(
                "Draftly task failed",
                task=name,
            )
            raise

        logger.info(
            "Draftly task completed",
            task=name,
        )

        return result
