"""Memory-grounded node wrapper (plan §8.1 consumption).

Wraps a graph node so organizational memory (curated knowledge +
validated solutions) is recalled for the task and prepended to the
node's input as grounding context. Degrades silently when memory is
unavailable — grounding must never break a run.
"""

from __future__ import annotations

import logging
from typing import Any

from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
)

logger = logging.getLogger(__name__)

MAX_GROUNDING_ITEMS = 5
GROUNDING_HEADER = "Relevant organizational knowledge:"


class MemoryGroundedNode(MultiAgentBase):
    """Prepend recalled memory to the task, then delegate."""

    def __init__(self, inner: MultiAgentBase, memory: Any) -> None:
        self.name = getattr(inner, "name", "grounded")
        self.inner = inner
        self.memory = memory

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        grounded_task = await self._ground(task)
        return await self.inner.invoke_async(
            grounded_task,
            invocation_state=invocation_state,
            **kwargs,
        )

    async def _ground(self, task: Any) -> Any:
        if self.memory is None or not isinstance(task, str) or not task.strip():
            return task
        try:
            items = await self.memory.recall_knowledge(task, limit=MAX_GROUNDING_ITEMS)
        except Exception:
            logger.exception("memory_grounding_failed")
            return task
        if not items:
            return task

        lines = [GROUNDING_HEADER]
        for item in items[:MAX_GROUNDING_ITEMS]:
            content = str(item.get("content", "")).strip()
            if content:
                lines.append(f"- {content}")
        if len(lines) == 1:
            return task
        return "\n".join(lines) + "\n\n" + task
