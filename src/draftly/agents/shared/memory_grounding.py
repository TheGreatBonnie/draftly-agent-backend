"""Memory-grounded node wrapper (plan §8.1 consumption).

Wraps a graph node so organizational memory (curated knowledge +
validated solutions) is recalled for the task and prepended to the
node's input as grounding context. Degrades silently when memory is
unavailable — grounding must never break a run.
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.agent import Agent
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    Status,
)

logger = structlog.get_logger(__name__)

MAX_GROUNDING_ITEMS = 5
MAX_EPISODE_ITEMS = 2
MAX_PROCEDURE_ITEMS = 1
GROUNDING_HEADER = "Relevant organizational knowledge:"


class MemoryGroundedNode(MultiAgentBase):
    """Prepend recalled memory to the task, then delegate."""

    def __init__(self, inner: MultiAgentBase | Agent, memory: Any) -> None:
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
        result = await self.inner.invoke_async(
            grounded_task,
            invocation_state=invocation_state,
            **kwargs,
        )
        if isinstance(result, MultiAgentResult):
            return result
        return MultiAgentResult(status=getattr(result, "status", Status.COMPLETED))

    def _is_bundle(self) -> bool:
        memory = self.memory
        return memory is not None and any(
            getattr(memory, attr, None) is not None
            for attr in ("knowledge", "episodes", "procedures")
        )

    async def _ground(self, task: Any) -> Any:
        if self.memory is None or not isinstance(task, str) or not task.strip():
            return task
        try:
            if self._is_bundle():
                return await self._merge_sources(task, self.memory)
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

    async def _merge_sources(self, task: str, bundle: Any) -> str:
        """Merge facts + episodes + procedures into one grounding block.

        Every source is individually optional and individually fail-open;
        the combined block respects MAX_GROUNDING_ITEMS overall.
        """
        lines: list[str] = []
        budget = MAX_GROUNDING_ITEMS

        knowledge = getattr(bundle, "knowledge", None)
        if knowledge is not None:
            try:
                for item in list(await knowledge(task))[: max(budget, 0)]:
                    content = str(item.get("content", "")).strip()
                    if content:
                        lines.append(f"- {content}")
                        budget -= 1
            except Exception:
                logger.exception("grounding_knowledge_failed")

        episodes = getattr(bundle, "episodes", None)
        if episodes is not None and budget > 0:
            try:
                hits = await episodes(task, limit=MAX_EPISODE_ITEMS)
                relevant = [h for h in hits if h.get("trigger_summary") or h.get("summary")]
                if relevant:
                    lines.append("Similar past episode:")
                    for hit in relevant[: max(budget, 0)]:
                        summary = hit.get("trigger_summary") or hit.get("summary")
                        lines.append(f"- {summary}")
                        budget -= 1
            except Exception:
                logger.exception("grounding_episodes_failed")

        procedures = getattr(bundle, "procedures", None)
        if procedures is not None and budget > 0:
            try:
                procs = await procedures(task, limit=MAX_PROCEDURE_ITEMS)
                for proc in procs[: max(budget, 0)]:
                    desc = proc.get("pattern_description")
                    if desc:
                        lines.append(f"Applicable procedure: {desc}")
                        budget -= 1
            except Exception:
                logger.exception("grounding_procedures_failed")

        if not lines:
            return task
        return "\n".join([GROUNDING_HEADER, *lines]) + "\n\n" + task
