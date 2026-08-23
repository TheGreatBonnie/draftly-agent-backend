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
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import structlog
from strands.multiagent.base import Status

from draftly.events.dispatcher import EventDispatcher
from draftly.events.stream_envelope import StreamEnvelope, filter_graph_event
from draftly.observability.metrics import Metrics
from draftly.observability.metrics import metrics as _default_metrics
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)

GraphFactory = Callable[[str, str], Any]

# Injectable registry (tests swap this for an isolated instance).
_metrics: Metrics = _default_metrics


def extract_token_usage(graph_result: Any, *, model: str) -> dict[str, int]:
    """Sum accumulated token usage across agent nodes into the registry.

    Defensive by design: swarm/non-agent nodes and offline test doubles
    carry no ``metrics``; zero totals are not recorded. Per-model
    attribution comes from routing rows written in the same run.
    """
    del model  # attribution handled by routing telemetry; kept for call-site clarity
    totals = {"input": 0, "output": 0}
    for node in getattr(graph_result, "execution_order", None) or []:
        node_result = getattr(node, "result", None)
        metrics_obj = getattr(node_result, "metrics", None)
        usage = getattr(metrics_obj, "accumulated_usage", None)
        if isinstance(usage, dict):
            totals["input"] += int(usage.get("inputTokens") or 0)
            totals["output"] += int(usage.get("outputTokens") or 0)
    if totals["input"]:
        _metrics.increment("draftly_tokens_input_total", float(totals["input"]))
    if totals["output"]:
        _metrics.increment("draftly_tokens_output_total", float(totals["output"]))
    return totals


async def _post_run_memory(context: Any, state: Any, surface: str, *, hook: Any = None) -> None:
    """Record episode + enqueue memory candidates. Never raises."""
    try:
        if hook is not None:
            await hook(context, state, surface)
            return
        from draftly.workflows.post_run.candidate_extractor import (
            record_post_run_memory,
        )

        await record_post_run_memory(context, state, surface)
    except Exception:
        logger.warning("post_run_memory_failed", exc_info=True)


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
        publisher: Any = None,
    ) -> None:
        self.context = context
        self._graph_factory = graph_factory or _default_graph_factory(context)
        self.dispatcher = dispatcher or EventDispatcher()
        # When set, runs stream via graph.stream_async and publish filtered
        # envelopes; outcome handling below is identical on both paths.
        self.publisher = publisher

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
        started = time.monotonic()
        invocation_state = {
            "run_id": run_id,
            "review_policy": self.context.review_policy(),
            "delivery_summary": "",
            "evaluation": {},
            "evidence_count": 0,
            "source": str(event.get("source") or "github"),
            "event_type": str(event.get("event_type") or "unknown"),
            "project_id": str(event.get("project_id") or ""),
        }
        if self.publisher is not None:
            result = await self._invoke_streaming(
                graph, json.dumps(event), invocation_state, surface
            )
        else:
            result = await graph.invoke_async(
                json.dumps(event), invocation_state=invocation_state
            )
        await self._record_routing_outcome(
            run_id=run_id,
            success=result.status == Status.COMPLETED,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )
        try:
            extract_token_usage(
                result, model=str(getattr(self.context, "model", "unknown") or "unknown")
            )
        except Exception:
            logger.warning("token_usage_extract_failed run_id=%s", run_id, exc_info=True)
        state.result = result

        # 4. Handle the outcome.
        if result.status == Status.INTERRUPTED:
            await self._store_interrupts(run_id, surface, result, state)
            await self._mark(event, "pending_review")
            return state.finish(WorkflowStatus.PENDING_REVIEW)

        if result.status == Status.COMPLETED:
            await self._mark(event, "completed")
            await _post_run_memory(self.context, state, surface)
            return state.finish(WorkflowStatus.DELIVERED)

        failed = self._failed_node_ids(result)
        state.errors.extend(failed)
        await self._mark(event, "failed")
        return state.finish(WorkflowStatus.FAILED)

    async def _invoke_streaming(
        self,
        graph: Any,
        task: str,
        invocation_state: dict[str, Any],
        surface: str,
    ) -> Any:
        """Iterate graph.stream_async, publish envelopes, return GraphResult.

        The stream MUST end with a terminal event: either ``result`` (the
        original GraphResult object is recovered) or ``force_stop`` (a failed
        result is synthesized so outcome handling matches the invoke path).
        """
        seq = 0
        result: Any = None
        started_at = time.monotonic()
        ttft_recorded = False
        async for raw in graph.stream_async(task, invocation_state=invocation_state):
            shaped = raw if isinstance(raw, dict) else {}
            envelope = filter_graph_event(
                shaped,
                run_id=invocation_state["run_id"],
                surface=surface,
            )
            if envelope is not None:
                if not ttft_recorded and envelope.type == "text_delta":
                    ttft_recorded = True
                    _metrics.observe(
                        "draftly_run_ttft_ms", time.monotonic() - started_at
                    )
                seq += 1
                envelope.seq = seq
                await self._safe_publish(envelope)
            if isinstance(raw, dict):
                if "result" in raw:
                    result = raw["result"]
                elif raw.get("force_stop"):
                    _metrics.increment("draftly_limit_hits_total")
                    result = SimpleNamespace(
                        status=Status.FAILED,
                        interrupts=[],
                        execution_order=[],
                        failed_nodes=0,
                    )
        if result is None:
            raise RuntimeError(
                "stream ended without a result event "
                f"run_id={invocation_state['run_id']}"
            )
        return result

    async def _safe_publish(self, envelope: StreamEnvelope) -> None:
        try:
            await self.publisher.publish(envelope)
        except Exception:
            logger.warning(
                "runner_publish_failed run_id=%s seq=%s",
                envelope.run_id,
                envelope.seq,
                exc_info=True,
            )

    async def _record_routing_outcome(
        self, *, run_id: str, success: bool, latency_ms: float
    ) -> None:
        """Best-effort telemetry: never fail the workflow over bookkeeping."""
        decision = getattr(self.context, "routing_decision", None)
        if decision is None:
            return
        try:
            repositories = getattr(self.context, "repositories", None)
            if repositories is None:
                return
            # Telemetry aggregates are keyed by TASK type; profile is only a
            # display label (e.g. research tasks run on the reasoning profile).
            task_type = decision.task_type or decision.profile
            await repositories.routing.record({
                "request_id": run_id,
                "organization_id": None,
                "task_type": task_type,
                "selected_model": decision.selected_model,
                "provider": decision.provider,
                "score": decision.score,
                "candidates_considered": decision.candidates_considered,
                "profile": decision.profile,
                "reason_codes": list(decision.reason_codes),
                "fallback_chain": list(decision.fallback_chain),
                "estimated_cost": decision.estimated_cost,
                "estimated_latency_ms": decision.estimated_latency_ms,
                "latency_ms": latency_ms,
                "success": success,
            })
            await repositories.performance.record_outcome(
                task_type=task_type,
                model_name=decision.selected_model,
                success=success,
                latency_ms=latency_ms,
            )
        except Exception:
            logger.warning("routing_telemetry_failed", exc_info=True)

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
