"""Memory-grounded node wrapper (plan §8.1 consumption).

Wraps a graph node so organizational memory (curated knowledge +
validated solutions) is recalled for the task and prepended to the
node's input as grounding context. Degrades silently when memory is
unavailable — grounding must never break a run.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

import structlog
from strands.agent import Agent
from strands.agent.agent_result import AgentResult
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

logger = structlog.get_logger(__name__)

MAX_GROUNDING_ITEMS = 5
MAX_EPISODE_ITEMS = 2
MAX_PROCEDURE_ITEMS = 1
GROUNDING_HEADER = "Relevant organizational knowledge:"

#: Appended to the grounded task when the inner context agent lost its
#: EvidenceBundle to a Strands tool-input parse-drop (items==0). The agent is
#: asked to re-emit the bundle it already collected, not to invent evidence.
EVIDENCE_RETRY_SUFFIX = (
    "\n\nYour previous structured evidence output was lost before it reached "
    "the gate (its tool-input JSON did not parse). Re-emit the complete "
    "EvidenceBundle exactly as you collected it, ensuring items[] is present."
)


def _bundle_items_empty(structured: Any) -> bool:
    """True when a structured EvidenceBundle arrived with zero items.

    A Strands tool-input parse-drop deserializes the bundle from ``{}``, so a
    ``items==[]`` bundle is the signature of lost evidence rather than a
    genuine "nothing to cite" result.
    """
    if structured is None:
        return False
    items = (
        structured.get("items")
        if isinstance(structured, dict)
        else getattr(structured, "items", None)
    )
    return isinstance(items, list) and len(items) == 0


async def _call_source(source: Any, query: str, *, limit: int, org_id: str | None) -> Any:
    """Call memory sources with tenant scope while keeping legacy adapters valid."""
    kwargs: dict[str, Any] = {}
    try:
        parameters = inspect.signature(source).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if "limit" in parameters or accepts_kwargs:
        kwargs["limit"] = limit
    if org_id is not None and ("org_id" in parameters or accepts_kwargs):
        kwargs["org_id"] = org_id
    return await source(query, **kwargs)


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
        grounded_task = await self._ground(task, invocation_state=invocation_state)
        started = time.monotonic()
        result = await self.inner.invoke_async(
            grounded_task,
            invocation_state=invocation_state,
            **kwargs,
        )
        if (
            isinstance(result, AgentResult)
            and _bundle_items_empty(getattr(result, "structured_output", None))
        ):
            # Parse-drop signature: the EvidenceBundle arrived as items==[].
            # Re-emit once (transient loss), then degrade loudly if the agent
            # genuinely has no evidence — never silently waiver the gate's
            # coverage signals on a zero-item bundle.
            logger.warning(
                "memory_grounded_empty_evidence_retrying",
                agent=self.name,
            )
            result = await self.inner.invoke_async(
                f"{grounded_task}{EVIDENCE_RETRY_SUFFIX}",
                invocation_state=invocation_state,
                **kwargs,
            )
            if (
                isinstance(result, AgentResult)
                and _bundle_items_empty(getattr(result, "structured_output", None))
            ):
                logger.warning(
                    "memory_grounded_empty_evidence_degraded",
                    agent=self.name,
                )
        execution_ms = round((time.monotonic() - started) * 1000)

        if isinstance(result, MultiAgentResult):
            return result

        # Preserve the inner AgentResult payload untouched: the context agent's
        # structured_output (EvidenceBundle) must reach downstream nodes, and
        # `str(AgentResult)` only serializes it while it is still present.
        # Dropping it into an empty MultiAgentResult used to throw the evidence
        # away, starving the evaluator and looping runs into the timeout.
        if isinstance(result, AgentResult):
            usage = None
            metrics = None
            event_metrics = getattr(result, "metrics", None)
            if event_metrics is not None:
                usage = getattr(event_metrics, "accumulated_usage", None)
                metrics = getattr(event_metrics, "accumulated_metrics", None)
            status = (
                Status.INTERRUPTED
                if getattr(result, "stop_reason", None) == "interrupt"
                else Status.COMPLETED
            )
            structured = getattr(result, "structured_output", None)
            evidence_items = None
            if structured is not None:
                items = getattr(structured, "items", None)
                if isinstance(items, list):
                    evidence_items = len(items)
            if structured is None:
                # Plain-text degradation surfaces here: the inner agent did not
                # emit its structured_output, so downstream nodes (and the
                # deterministic evaluator) will only see raw prose.
                logger.warning(
                    "memory_grounded_no_structured_output",
                    agent=self.name,
                )
            else:
                logger.info(
                    "memory_grounded_structured_preserved",
                    agent=self.name,
                    structured=True,
                    evidence_items=evidence_items,
                )
            inner = NodeResult(
                result=result,
                execution_time=execution_ms,
                status=status,
                execution_count=1,
            )
            kwargs_result: dict[str, Any] = {}
            if usage is not None:
                kwargs_result["accumulated_usage"] = usage
            if metrics is not None:
                kwargs_result["accumulated_metrics"] = metrics
            return MultiAgentResult(
                status=status,
                results={getattr(result, "agent_name", None) or self.name: inner},
                execution_time=execution_ms,
                **kwargs_result,
            )
        return MultiAgentResult(status=getattr(result, "status", Status.COMPLETED))

    def _is_bundle(self) -> bool:
        memory = self.memory
        return memory is not None and any(
            getattr(memory, attr, None) is not None
            for attr in ("knowledge", "episodes", "procedures")
        )

    async def _ground(
        self,
        task: Any,
        *,
        invocation_state: dict[str, Any] | None = None,
    ) -> Any:
        if self.memory is None or not isinstance(task, str) or not task.strip():
            return task
        state = invocation_state or {}
        org_id = state.get("project_id") or state.get("org_id")
        try:
            if self._is_bundle():
                return await self._merge_sources(task, self.memory, org_id=org_id)
            if org_id is None:
                items = await self.memory.recall_knowledge(
                    task,
                    limit=MAX_GROUNDING_ITEMS,
                )
            else:
                items = await self.memory.recall_knowledge(
                    task,
                    limit=MAX_GROUNDING_ITEMS,
                    org_id=org_id,
                )
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

    async def _merge_sources(
        self,
        task: str,
        bundle: Any,
        *,
        org_id: str | None = None,
    ) -> str:
        """Merge facts + episodes + procedures into one grounding block.

        Every source is individually optional and individually fail-open;
        the combined block respects MAX_GROUNDING_ITEMS overall.
        """
        lines: list[str] = []
        budget = MAX_GROUNDING_ITEMS

        knowledge = getattr(bundle, "knowledge", None)
        if knowledge is not None:
            try:
                for item in list(
                    await _call_source(
                        knowledge,
                        task,
                        limit=max(budget, 0),
                        org_id=org_id,
                    )
                )[: max(budget, 0)]:
                    content = str(item.get("content", "")).strip()
                    if content:
                        lines.append(f"- {content}")
                        budget -= 1
            except Exception:
                logger.exception("grounding_knowledge_failed")

        episodes = getattr(bundle, "episodes", None)
        if episodes is not None and budget > 0:
            try:
                hits = await _call_source(
                    episodes,
                    task,
                    limit=MAX_EPISODE_ITEMS,
                    org_id=org_id,
                )
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
                procs = await _call_source(
                    procedures,
                    task,
                    limit=MAX_PROCEDURE_ITEMS,
                    org_id=org_id,
                )
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
