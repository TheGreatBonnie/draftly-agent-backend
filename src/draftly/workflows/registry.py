"""WorkflowRegistry: workflow type → runner function (plan §7.2)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

WorkflowFunc = Callable[..., Awaitable[Any]]


class WorkflowRegistry:
    """Maps canonical workflow names to async workflow functions.

    Attribute access falls back to the registry so legacy callers
    (``app/composition/workers.py`` TASK_REGISTRY) keep working.
    """

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowFunc] = {}

    def register(self, name: str, workflow: WorkflowFunc) -> None:
        if name in self._workflows:
            raise ValueError(f"workflow already registered: {name}")
        self._workflows[name] = workflow

    def get(self, name: str) -> WorkflowFunc | None:
        return self._workflows.get(name)

    def names(self) -> list[str]:
        return sorted(self._workflows)

    async def run(self, name: str, *args: Any, **kwargs: Any) -> Any:
        workflow = self.get(name)
        if workflow is None:
            raise KeyError(f"unknown workflow: {name}")
        return await workflow(*args, **kwargs)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._workflows

    def __getattr__(self, name: str) -> WorkflowFunc:
        # Only called when normal attribute lookup fails.
        try:
            return self.__dict__["_workflows"][name]
        except KeyError:
            raise AttributeError(
                f"WorkflowRegistry has no workflow named {name!r}"
            ) from None
