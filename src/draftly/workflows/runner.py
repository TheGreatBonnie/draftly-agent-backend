"""WorkflowRunner (plan §7.4) — replaces the pipelines layer.

One run = one session = one graph (§6.8). Idempotency is claimed BEFORE
the graph is touched: an atomic INSERT..ON CONFLICT DO NOTHING on the
events table decides whether this delivery owns the run.

The runner is storage-agnostic through ``WorkflowContext.repositories``
(duck-typed: needs ``.events`` with try_claim/mark_status and ``.reviews``
with store_interrupt) and test-injectable via ``graph_factory``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from strands.multiagent.base import Status

from draftly.events.dispatcher import EventDispatcher
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)

GraphFactory = Callable[[str, str], Any]


def _default_graph_factory(context: WorkflowContext) -> GraphFactory:
    """Build the real per-run graph via the Phase 4 integration layer."""

    def factory(run_id: str, surface: str) -> Any:
        from draftly.integrations.strands.graph import build_graph_for_run

        return build_graph_for_run(
            run_id,
            surface=surface,
            tools_registry=context.tools,
            model=context.model,
            hooks=context.hooks,
            storage_dir=context.storage_dir,
            audit_repo=context.audit_repo,
            memory=getattr(context, "memory", None),
            **context.graph_limits(),
        )

    return factory


class WorkflowRunner:
    """Build the per-run graph, invoke it, and handle the outcome."""

    def __init__(
        self,
        context: WorkflowContext,
        *,
        graph_factory: GraphFactory | None = None,
        dispatcher: EventDispatcher | None = None,
    ) -> None:
        self.context = context
        self._graph_factory = graph_factory or _default_graph_factory(context)
        self.dispatcher = dispatcher or EventDispatcher()

    async def run(self, event: dict[str, Any]) -> WorkflowState:
        """Execute one normalized event end-to-end."""
        event = dict(event)
        run_id = str(event.get("event_id") or uuid4())
        state = WorkflowState(run_id=run_id, event=event)

        surface = self.dispatcher.route(event)
        if surface is None:
            return state.finish(WorkflowStatus.SKIPPED)
        state.surface = surface

        # 1. Idempotency: claim the event before touching the graph.
        if not await self._claim(event, run_id):
            existing = await self._existing(event.get("event_id", ""))
            if existing is not None:
                state.status = WorkflowStatus.DUPLICATE
                state.errors.append(f"event already recorded: {existing}")
            else:
                state.status = WorkflowStatus.DUPLICATE
            logger.info("runner_duplicate run_id=%s", run_id)
            return state

        # 2. One session + one graph for this run's surface.
        graph = self._graph_factory(run_id, surface)

        # 3. Invoke; runtime context rides in invocation_state, never in
        #    the prompt. ReviewGate reads review_policy before delivering.
        result = await graph.invoke_async(
            json.dumps(event),
            invocation_state={
                "run_id": run_id,
                "review_policy": self.context.review_policy(),
                "delivery_summary": "",
                "evaluation": {},
                "evidence_count": 0,
                "source": str(event.get("source") or "github"),
                "event_type": str(event.get("event_type") or "unknown"),
                "project_id": str(event.get("project_id") or ""),
            },
        )
        state.result = result

        # 4. Handle the outcome.
        if result.status == Status.INTERRUPTED:
            await self._store_interrupts(run_id, surface, result, state)
            await self._mark(event, "pending_review")
            return state.finish(WorkflowStatus.PENDING_REVIEW)

        if result.status == Status.COMPLETED:
            await self._mark(event, "completed")
            return state.finish(WorkflowStatus.DELIVERED)

        failed = self._failed_node_ids(result)
        state.errors.extend(failed)
        await self._mark(event, "failed")
        return state.finish(WorkflowStatus.FAILED)

    @staticmethod
    def _failed_node_ids(result: Any) -> list[str]:
        """Extract failed node ids from a graph result.

        ``GraphResult.failed_nodes`` is a COUNT (int); the identities live
        on ``execution_order`` entries via ``execution_status``. Test
        doubles that carry a collection on ``failed_nodes`` also work.
        """
        order = getattr(result, "execution_order", None) or []
        from_order = [
            node.node_id
            for node in order
            if getattr(node, "execution_status", None) == Status.FAILED
        ]
        if from_order:
            return sorted(from_order)

        raw = getattr(result, "failed_nodes", None)
        if isinstance(raw, int):
            return []
        return sorted(getattr(node, "node_id", "?") for node in (raw or ()))

    # ========================================================
    # Persistence helpers (duck-typed repositories)
    # ========================================================

    async def _claim(self, event: dict[str, Any], run_id: str) -> bool:
        events = self.context.events
        if events is None:
            return True  # no persistence wired (tests): always run
        try:
            return await events.try_claim(
                run_id,
                source=str(event.get("source") or "github"),
                event_type=str(event.get("event_type") or "unknown"),
                repository=event.get("repository"),
                actor=event.get("actor"),
                payload=event,
                org_id=event.get("project_id"),
            )
        except Exception:
            logger.exception("runner_claim_failed run_id=%s", run_id)
            raise

    async def _existing(self, event_id: str) -> str | None:
        events = self.context.events
        if events is None or not event_id:
            return None
        finder = getattr(events, "find_by_event_id", None)
        if finder is None:
            return None
        row = await finder(event_id)
        return str(row.get("status")) if isinstance(row, dict) else None

    async def _mark(self, event: dict[str, Any], status: str) -> None:
        events = self.context.events
        if events is None:
            return
        marker = getattr(events, "mark_status", None)
        if marker is None:
            return
        await marker(str(event.get("event_id")), status)

    async def _store_interrupts(
        self,
        run_id: str,
        surface: str,
        result: Any,
        state: WorkflowState,
    ) -> None:
        reviews = self.context.reviews
        for interrupt in result.interrupts or []:
            record = {
                "interrupt_id": interrupt.id,
                "reason": interrupt.reason,
            }
            state.interrupts.append(record)
            if reviews is None:
                continue
            try:
                await reviews.store_interrupt(
                    run_id=run_id,
                    interrupt_id=interrupt.id,
                    reason=interrupt.reason,
                    workflow_type=surface,
                    org_id=str(state.event.get("project_id") or ""),
                )
            except Exception:
                logger.exception("store_interrupt_failed run_id=%s", run_id)
