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
import re
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import structlog
from strands.multiagent.base import Status

from draftly.delivery.models import PullRequestResult, SupportDeliveryReceipt
from draftly.events.dispatcher import EventDispatcher
from draftly.events.stream_envelope import (
    StreamEnvelope,
    filter_graph_event,
    steering_envelope,
)
from draftly.integrations.github.runtime import (
    reset_installation_id,
    set_installation_id,
)
from draftly.integrations.support.runtime import (
    reset_support_runtime,
    set_support_runtime,
    support_runtime_for,
)
from draftly.memory.scope import (
    memory_scope_for,
    reset_memory_scope,
    set_memory_scope,
)
from draftly.observability.metrics import Metrics
from draftly.observability.metrics import metrics as _default_metrics
from draftly.workflows.context import WorkflowContext
from draftly.workflows.grounding import (
    repo_checkout_for,
    reset_grounding,
    resolve_grounding,
    set_grounding,
)
from draftly.workflows.state import WorkflowState, WorkflowStatus
from draftly.workflows.steering_scope import (
    SteeringRunScope,
    current_steering_scope,
    reset_steering_scope,
    set_steering_scope,
)

logger = structlog.get_logger(__name__)

GraphFactory = Callable[[str, str], Any]


class InterventionResumeError(ValueError):
    """A steering intervention could not be claimed or resumed."""


# Injectable registry (tests swap this for an isolated instance).
_metrics: Metrics = _default_metrics

_NODE_TIMEOUT_RE = re.compile(r"Node '([^']+)' execution timed out after (\d+)s")


def _node_timeout_node_id(exc: Exception) -> str | None:
    """Return the node id from a Strands node-timeout exception, or None."""
    match = _NODE_TIMEOUT_RE.search(str(exc))
    return match.group(1) if match is not None else None


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


def delivery_receipt_from_result(graph_result: Any) -> dict[str, Any] | None:
    """Extract the deliver node's structured receipt (dict) from a graph result.

    Returns ``None`` when no deliver node ran, or its result carries no
    parsable structured output. Used both to route terminal status
    (``blocked`` is NOT ``delivered``) and to persist the receipt.
    """
    for node in getattr(graph_result, "execution_order", None) or []:
        if str(getattr(node, "node_id", "")) != "deliver":
            continue
        structured = getattr(getattr(node, "result", None), "structured_output", None)
        if hasattr(structured, "model_dump"):
            structured = structured.model_dump()
        if isinstance(structured, dict):
            return structured
    return None


def is_blocked_delivery(receipt: dict[str, Any] | None) -> bool:
    """True when the delivery agent explicitly refused to act (blocked).

    Blocks are a distinct terminal outcome: the graph completed, but nothing
    was delivered. Treating a blocked receipt as ``delivered`` (live run
    7ddccdd0) silently lies to the reviewer/notifier.
    """
    if not receipt:
        return False
    return str(receipt.get("status") or "").lower() == "blocked"


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


def _workflow_key_for(event: dict[str, Any], surface: str) -> str:
    """Default workflow key: explicit key, else event-type default, else surface."""
    workflow_key = str(event.get("workflow_key") or "")
    if workflow_key:
        return workflow_key
    event_type = str(event.get("event_type") or "")
    return (
        "github_release"
        if event_type.startswith("release")
        else "github_issue"
        if event_type.startswith("issues")
        else "github_pr"
        if event_type.startswith("pull_request")
        else surface
    )


class _RunStreamSeq:
    """Per-run monotonic envelope sequence shared by stream and steering.

    One allocator is created per graph build and handed to both the
    ``_invoke_streaming`` loop and the steering event sink, so every envelope
    for a run (graph frames and steering decisions) gets a strictly increasing,
    collision-free ``seq``. SSE replay and reconnect deduplication rely on this
    single monotonic counter per run.
    """

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        self._value += 1
        return self._value


def _steering_event_sink(
    *,
    runtime: Any,
    publisher: Any,
    run_id: str,
    surface: str,
    stream_seq: _RunStreamSeq,
) -> Callable[..., Any]:
    """Wire ONE redacted steering event per decision onto the run stream."""

    async def sink(
        decision: Any,
        *,
        tool_name: str | None = None,
        node_id: str | None = None,
        agent_id: str | None = None,
        attempt_summary: dict[str, Any] | None = None,
    ) -> None:
        envelope = steering_envelope(
            decision,
            run_id=run_id,
            surface=surface,
            node_id=node_id,
            agent_id=agent_id,
            tool_name=tool_name,
            attempt_summary=attempt_summary,
            payload_max_bytes=runtime.config.payload_max_bytes,
        )
        envelope.seq = stream_seq.next()
        try:
            await publisher.publish(envelope)
        except Exception:
            _metrics.increment("draftly_steering_publish_failures_total")
            logger.warning(
                "steering_event_publish_failed",
                run_id=run_id,
                seq=envelope.seq,
                exc_info=True,
            )

    return sink


def _default_graph_factory(context: WorkflowContext) -> GraphFactory:
    """Build the real per-run graph via the Phase 4 integration layer."""

    def factory(run_id: str, surface: str) -> Any:
        from draftly.integrations.strands.graph import build_graph_for_run
        from draftly.workflows.grounding import current_grounding

        grounding = current_grounding()
        steering_scope = current_steering_scope()
        steering_runtime = None
        stream_seq = _RunStreamSeq()
        if steering_scope is not None:
            steering_runtime = context.new_steering_runtime(
                run_id,
                surface,
                org_id=steering_scope.org_id,
                project_id=steering_scope.project_id,
                workflow_key=steering_scope.workflow_key,
            )
            publisher = getattr(context, "publisher", None)
            if steering_runtime is not None and publisher is not None:
                steering_runtime = steering_runtime.with_sinks(
                    event_sink=_steering_event_sink(
                        runtime=steering_runtime,
                        publisher=publisher,
                        run_id=run_id,
                        surface=surface,
                        stream_seq=stream_seq,
                    )
                )

        jobs_repo = getattr(getattr(context, "repositories", None), "jobs", None)
        graph = build_graph_for_run(
            run_id,
            surface=surface,
            tools_registry=context.tools,
            agents=context.agents,
            model=context.model,
            hooks=context.hooks,
            storage_dir=context.storage_dir,
            session_repository=getattr(context, "session_repository", None),
            audit_repo=context.audit_repo,
            memory=context.memory_bundle(),
            publisher=getattr(context, "publisher", None),
            jobs_repo=jobs_repo,
            grounding=grounding.get("mode", "local"),
            repo_dir=grounding.get("repo_dir"),
            steering_runtime=steering_runtime,
            **context.graph_limits(),
        )
        graph._draftly_stream_seq = stream_seq
        return graph

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
        self._session_repository = getattr(context, "session_repository", None)
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
            logger.warning(
                "workflow_skipped_unknown_surface",
                run_id=run_id,
                event_type=event.get("event_type"),
            )
            return state.finish(WorkflowStatus.SKIPPED)
        state.surface = surface
        logger.info(
            "workflow_started",
            run_id=run_id,
            event_type=event.get("event_type"),
            surface=surface,
        )

        # Only opened PRs run the documentation graph; other PR
        # actions skip before the idempotency claim so they leave no
        # audit/duplicate record. Gate on the event prefix (not the surface)
        # so push/release events that share the "pull_request" surface still
        # run.
        event_type = str(event.get("event_type") or "")
        if event_type.split(".")[0] == "pull_request" and event_type != "pull_request.opened":
            logger.info(
                "workflow_skipped_pr_not_merged",
                run_id=run_id,
                event_type=event_type,
                surface=surface,
            )
            return state.finish(WorkflowStatus.SKIPPED)

        # 1. Idempotency: claim the event before touching the graph.
        if not await self._claim(event, run_id):
            existing = await self._existing(event.get("event_id", ""))
            if existing is not None:
                state.status = WorkflowStatus.DUPLICATE
                state.errors.append(f"event already recorded: {existing}")
            else:
                state.status = WorkflowStatus.DUPLICATE
            logger.info(
                "workflow_duplicate",
                run_id=run_id,
                event_type=event_type,
            )
            return state

        existing = await self._existing_support_delivery(event)
        if existing is not None:
            state.status = WorkflowStatus.DUPLICATE
            state.result = {"receipt": existing}
            logger.info(
                "support_delivery_already_exists",
                run_id=run_id,
                surface=surface,
            )
            return state

        # The canonical run is claimed before graph construction. This keeps
        # retries/idempotent webhook deliveries tied to one durable resource
        # while the existing provider/audit stores continue to be populated.
        await self._ensure_canonical_run(event, run_id, surface)

        # 2. One session + one graph for this run's surface.
        routing_decisions: dict[str, Any] = {}

        def collect_routing_decision(role: str, decision: Any) -> None:
            routing_decisions[role] = decision

        from draftly.integrations.strands.models import routing_decision_scope

        repo_dir = repo_checkout_for(event)
        if repo_dir and not event.get("repo_dir"):
            # Surface the discovered checkout into the task so the LOCAL note
            # can point evidence agents at the real path (never a guess).
            event["repo_dir"] = repo_dir
        grounding = {
            "mode": resolve_grounding(
                repo_dir=repo_dir,
                installation_id=event.get("installation_id"),
            ),
            "repo_dir": event.get("repo_dir"),
        }
        build_token = set_support_runtime(support_runtime_for(event))
        build_memory_token = set_memory_scope(memory_scope_for(event, surface))
        grounding_token = set_grounding(grounding)
        steering_token = set_steering_scope(
            SteeringRunScope(
                run_id=run_id,
                surface=surface,
                org_id=str(event.get("project_id") or ""),
                project_id=str(event.get("project_id") or ""),
                workflow_key=_workflow_key_for(event, surface),
            )
        )
        try:
            with routing_decision_scope(collect_routing_decision):
                graph = self._graph_factory(run_id, surface)
        finally:
            reset_steering_scope(steering_token)
            reset_grounding(grounding_token)
            reset_memory_scope(build_memory_token)
            reset_support_runtime(build_token)
        await self._persist_lifecycle(event, "running", run_id=run_id)
        await self._broadcast_lifecycle(
            org_id=str(event.get("project_id") or ""),
            run_id=run_id,
            status="running",
            surface=surface,
        )

        # 3. Invoke; runtime context rides in invocation_state, never in
        #    the prompt. ReviewGate reads review_policy before delivering.
        started = time.monotonic()
        invocation_state = self._invocation_state(event, surface)
        installation_token = set_installation_id(event.get("installation_id"))
        support_token = set_support_runtime(support_runtime_for(event))
        memory_token = set_memory_scope(memory_scope_for(event, surface))
        try:
            if self.publisher is not None:
                result = await self._invoke_streaming(
                    graph, json.dumps(event), invocation_state, surface
                )
            else:
                try:
                    result = await graph.invoke_async(
                        json.dumps(event), invocation_state=invocation_state
                    )
                except Exception as exc:
                    node_id = _node_timeout_node_id(exc)
                    if node_id is None:
                        raise
                    _metrics.increment("draftly_node_timeouts_total")
                    logger.warning(
                        "workflow_node_timeout",
                        run_id=run_id,
                        node_id=node_id,
                        error=str(exc),
                    )
                    result = SimpleNamespace(
                        status=Status.FAILED,
                        interrupts=[],
                        execution_order=[],
                        failed_nodes=[SimpleNamespace(node_id=node_id)],
                    )
        finally:
            reset_support_runtime(support_token)
            reset_memory_scope(memory_token)
            reset_installation_id(installation_token)
        await self._record_routing_outcome(
            run_id=run_id,
            success=result.status == Status.COMPLETED,
            latency_ms=(time.monotonic() - started) * 1000.0,
            decisions=routing_decisions,
            organization_id=str(event.get("project_id") or "") or None,
        )
        try:
            extract_token_usage(
                result, model=str(getattr(self.context, "model", "unknown") or "unknown")
            )
        except Exception:
            logger.warning(
                "token_usage_extract_failed",
                run_id=run_id,
                exc_info=True,
            )
        state.result = result

        # 4. Await the audit flush so agent_runs rows survive loop teardown,
        #    then handle the outcome.
        await self._drain_audit_flush(graph, run_id)
        return await self._finish_result(event, surface, result, state)

    async def resume_review(
        self,
        *,
        event: dict[str, Any],
        interrupt_id: str,
        response: dict[str, Any],
    ) -> WorkflowState:
        """Resume a paused graph through the same lifecycle as a fresh run."""
        event = dict(event)
        run_id = str(event.get("event_id") or "")
        surface = self.dispatcher.route(event)
        if not run_id or surface is None:
            raise ValueError("Cannot resume a workflow without a valid run and surface")

        logger.info("workflow_resume_started", run_id=run_id, surface=surface)

        routing_decisions: dict[str, Any] = {}

        def collect_routing_decision(role: str, decision: Any) -> None:
            routing_decisions[role] = decision

        from draftly.integrations.strands.models import routing_decision_scope

        repo_dir = repo_checkout_for(event)
        if repo_dir and not event.get("repo_dir"):
            event["repo_dir"] = repo_dir
        grounding = {
            "mode": resolve_grounding(
                repo_dir=repo_dir,
                installation_id=event.get("installation_id"),
            ),
            "repo_dir": event.get("repo_dir"),
        }
        grounding_token = set_grounding(grounding)
        steering_token = set_steering_scope(
            SteeringRunScope(
                run_id=run_id,
                surface=surface,
                org_id=str(event.get("project_id") or ""),
                project_id=str(event.get("project_id") or ""),
                workflow_key=_workflow_key_for(event, surface),
            )
        )
        try:
            with routing_decision_scope(collect_routing_decision):
                graph = self._graph_factory(run_id, surface)
        finally:
            reset_steering_scope(steering_token)
            reset_grounding(grounding_token)
        invocation_state = self._invocation_state(event, surface)
        resume_input = [
            {
                "interruptResponse": {
                    "interruptId": interrupt_id,
                    "response": response,
                }
            }
        ]
        # Preflight: a resume is only valid when the graph restored the
        # interrupted state from its persisted session. When the session is
        # gone (fresh session created during this request), the graph would
        # treat the resume payload as a brand-new task and run it from the
        # entry points — failing later in an unrelated node. Detect that
        # early and keep the review actionable instead.
        resumable = self._session_is_resumable(graph, interrupt_id)
        if not resumable:
            from draftly.review.resume import ReviewResumeError

            message = "interrupted session state not found; the run cannot be resumed"
            logger.warning(
                "review_resume_blocked",
                run_id=run_id,
                surface=surface,
                interrupt_id=interrupt_id,
                reason="session_lost",
            )
            await self._mark(event, "pending_review")
            await self._persist_lifecycle(
                event,
                "pending_review",
                run_id=run_id,
                error=message,
                result={"status": "SESSION_LOST", "error": message},
            )
            await self._broadcast_lifecycle(
                org_id=str(event.get("project_id") or ""),
                run_id=run_id,
                status="pending_review",
                surface=surface,
            )
            await self._notify_reviewers(run_id)
            raise ReviewResumeError(f"Cannot resume run {run_id}: {message}")
        state = WorkflowState(run_id=run_id, event=event, surface=surface)
        started = time.monotonic()
        installation_token = set_installation_id(event.get("installation_id"))
        support_token = set_support_runtime(support_runtime_for(event))
        memory_token = set_memory_scope(memory_scope_for(event, surface))
        try:
            if self.publisher is not None:
                result = await self._invoke_streaming(
                    graph, resume_input, invocation_state, surface
                )
            else:
                result = await graph.invoke_async(
                    resume_input,
                    invocation_state=invocation_state,
                )
            await self._record_routing_outcome(
                run_id=run_id,
                success=result.status == Status.COMPLETED,
                latency_ms=(time.monotonic() - started) * 1000.0,
                decisions=routing_decisions,
                organization_id=str(event.get("project_id") or "") or None,
            )
        except Exception as exc:
            state.errors.append(str(exc))
            if response.get("approved") is True:
                # Keep the review actionable when an approval cannot be
                # resumed (for example, a transient graph/session failure).
                # The route must not record an approval for work that did not
                # reach delivery.
                await self._mark(event, "pending_review")
                await self._persist_lifecycle(
                    event,
                    "pending_review",
                    run_id=run_id,
                    error=str(exc),
                    result={"status": "RESUME_FAILED", "error": str(exc)},
                )
                await self._broadcast_lifecycle(
                    org_id=str(event.get("project_id") or ""),
                    run_id=run_id,
                    status="pending_review",
                    surface=surface,
                )
                await self._notify_reviewers(run_id)
                logger.warning(
                    "workflow_review_approval_resume_failed",
                    run_id=run_id,
                    error=str(exc),
                )
                return state.finish(WorkflowStatus.PENDING_REVIEW)
            await self._mark(event, "failed")
            await self._persist_lifecycle(
                event,
                "failed",
                run_id=run_id,
                error=str(exc),
                result={"status": "FAILED", "error": str(exc)},
            )
            await self._broadcast_lifecycle(
                org_id=str(event.get("project_id") or ""),
                run_id=run_id,
                status="failed",
                surface=surface,
            )
            logger.warning("workflow_review_resume_failed", run_id=run_id, error=str(exc))
            return state.finish(WorkflowStatus.FAILED)
        finally:
            reset_support_runtime(support_token)
            reset_memory_scope(memory_token)
            reset_installation_id(installation_token)

        await self._drain_audit_flush(graph, run_id)
        resumed = await self._finish_result(event, surface, result, state)
        logger.info("workflow_resume_done", run_id=run_id, status=resumed.status.value)
        return resumed

    async def resume_intervention(
        self,
        *,
        event: dict[str, Any],
        interrupt_id: str,
        response: dict[str, Any],
        graph_factory: GraphFactory | None = None,
    ) -> WorkflowState:
        """Resolve a durable steering intervention and resume its run.

        The intervention row is the single source of truth: a pending row is
        claimed atomically by (org, idempotency_key) before the graph is
        touched. An identical replay returns the already-known terminal outcome
        without invoking the graph again; a conflicting replay, an expired or
        missing row, or an unserializable session all fail this call while
        leaving the run in a consistent state.
        """
        event = dict(event)
        run_id = str(event.get("event_id") or "")
        surface = self.dispatcher.route(event)
        if not run_id or surface is None:
            raise InterventionResumeError(
                "Cannot resume an intervention without a valid run and surface"
            )
        if not interrupt_id:
            raise InterventionResumeError("interrupt_id is required")
        if not isinstance(response, dict) or not response:
            raise InterventionResumeError("a non-empty response dict is required")

        org_id = str(event.get("project_id") or "")
        interventions = getattr(
            getattr(self.context, "repositories", None), "steering_interventions", None
        )
        if interventions is None:
            raise InterventionResumeError("steering interventions are unavailable")

        action = str(response.get("action") or "")
        message = response.get("message")
        if action not in {"approve", "approve_and_review", "deny", "denied", "guide", "cancel"}:
            raise InterventionResumeError(f"unsupported intervention action '{action}'")
        idempotency_key = str(response.get("idempotency_key") or "") or f"{org_id}:{interrupt_id}"

        logger.info(
            "workflow_intervention_resume_started",
            run_id=run_id,
            surface=surface,
            interrupt_id=interrupt_id,
            action=action,
        )
        _metrics.increment("draftly_intervention_resume_total")

        pending = await self._safe_intervention_lookup(interventions, run_id, interrupt_id, org_id)
        if pending is not None and self._intervention_expired(pending):
            _metrics.increment("draftly_steering_interrupts_expired_total")
            _metrics.increment("draftly_intervention_resume_failures_total")
            try:
                await interventions.resolve(
                    intervention_id=str(pending.id),
                    status="expired",
                    resolver_id="system:expired",
                )
            except Exception:
                pass
            raise InterventionResumeError(f"intervention {interrupt_id} is expired")

        await self._claim_intervention(
            interventions,
            run_id=run_id,
            interrupt_id=interrupt_id,
            org_id=org_id,
            idempotency_key=idempotency_key,
            action=action,
            message=str(message) if message is not None else None,
        )
        if pending is not None:
            # A pending row was atomically reconciled; count the side-effect
            # resolution (approve/deny) separately from replays of already
            # resolved rows below.
            _metrics.increment("draftly_side_effect_reconciled_total")
        if pending is None:
            # An identical earlier claim already resolved the row; surface its
            # terminal outcome without resuming the graph a second time.
            logger.info(
                "workflow_intervention_resume_replayed",
                run_id=run_id,
                interrupt_id=interrupt_id,
                action=action,
            )
            _metrics.increment("draftly_intervention_resume_success_total")
            return await self._replay_outcome(event, run_id, surface)

        approved = action in {"approve", "approve_and_review"}
        routing_decisions: dict[str, Any] = {}

        def collect_routing_decision(role: str, decision: Any) -> None:
            routing_decisions[role] = decision

        from draftly.integrations.strands.models import routing_decision_scope

        repo_dir = repo_checkout_for(event)
        if repo_dir and not event.get("repo_dir"):
            event["repo_dir"] = repo_dir
        grounding = {
            "mode": resolve_grounding(
                repo_dir=repo_dir,
                installation_id=event.get("installation_id"),
            ),
            "repo_dir": event.get("repo_dir"),
        }
        grounding_token = set_grounding(grounding)
        steering_token = set_steering_scope(
            SteeringRunScope(
                run_id=run_id,
                surface=surface,
                org_id=org_id,
                project_id=org_id,
                workflow_key=_workflow_key_for(event, surface),
            )
        )
        try:
            with routing_decision_scope(collect_routing_decision):
                graph = (graph_factory or self._graph_factory)(run_id, surface)
        finally:
            reset_steering_scope(steering_token)
            reset_grounding(grounding_token)

        invocation_state = self._invocation_state(event, surface)
        resume_input = [
            {
                "interruptResponse": {
                    "interruptId": interrupt_id,
                    "response": {
                        "approved": approved,
                        "action": action,
                        "message": message,
                    },
                }
            }
        ]
        resumable = self._session_is_resumable(graph, interrupt_id)
        if not resumable:
            message = "interrupted session state not found; the run cannot be resumed"
            logger.warning(
                "workflow_intervention_resume_blocked",
                run_id=run_id,
                surface=surface,
                interrupt_id=interrupt_id,
                reason="session_lost",
            )
            state = WorkflowState(run_id=run_id, event=event, surface=surface)
            _metrics.increment("draftly_intervention_resume_failures_total")
            await self._mark(event, "failed")
            await self._persist_lifecycle(
                event,
                "failed",
                run_id=run_id,
                error=message,
                result={"status": "SESSION_LOST", "error": message},
            )
            await self._broadcast_lifecycle(
                org_id=org_id,
                run_id=run_id,
                status="failed",
                surface=surface,
            )
            return state.finish(WorkflowStatus.FAILED)

        state = WorkflowState(run_id=run_id, event=event, surface=surface)
        started = time.monotonic()
        installation_token = set_installation_id(event.get("installation_id"))
        support_token = set_support_runtime(support_runtime_for(event))
        memory_token = set_memory_scope(memory_scope_for(event, surface))
        try:
            if self.publisher is not None:
                result = await self._invoke_streaming(
                    graph, resume_input, invocation_state, surface
                )
            else:
                result = await graph.invoke_async(
                    resume_input,
                    invocation_state=invocation_state,
                )
            await self._record_routing_outcome(
                run_id=run_id,
                success=result.status == Status.COMPLETED,
                latency_ms=(time.monotonic() - started) * 1000.0,
                decisions=routing_decisions,
                organization_id=org_id or None,
            )
        except Exception as exc:
            state.errors.append(str(exc))
            _metrics.increment("draftly_intervention_resume_failures_total")
            await self._mark(event, "failed")
            await self._persist_lifecycle(
                event,
                "failed",
                run_id=run_id,
                error=str(exc),
                result={"status": "RESUME_FAILED", "error": str(exc)},
            )
            await self._broadcast_lifecycle(
                org_id=org_id,
                run_id=run_id,
                status="failed",
                surface=surface,
            )
            logger.warning(
                "workflow_intervention_resume_failed",
                run_id=run_id,
                interrupt_id=interrupt_id,
                error=str(exc),
            )
            return state.finish(WorkflowStatus.FAILED)
        finally:
            reset_support_runtime(support_token)
            reset_memory_scope(memory_token)
            reset_installation_id(installation_token)

        await self._drain_audit_flush(graph, run_id)
        resumed = await self._finish_result(event, surface, result, state)
        _metrics.increment("draftly_intervention_resume_success_total")
        logger.info(
            "workflow_intervention_resume_done",
            run_id=run_id,
            status=resumed.status.value,
        )
        return resumed

    async def _safe_intervention_lookup(
        self,
        interventions: Any,
        run_id: str,
        interrupt_id: str,
        org_id: str,
    ) -> Any:
        try:
            return await interventions.get_pending(
                run_id=run_id, interrupt_id=interrupt_id, org_id=org_id
            )
        except Exception as exc:
            raise InterventionResumeError(f"intervention lookup failed: {exc}") from exc

    async def _claim_intervention(
        self,
        interventions: Any,
        *,
        run_id: str,
        interrupt_id: str,
        org_id: str,
        idempotency_key: str,
        action: str,
        message: str | None,
    ) -> Any:
        try:
            return await interventions.claim_response(
                run_id=run_id,
                interrupt_id=interrupt_id,
                org_id=org_id,
                idempotency_key=idempotency_key,
                action=action,
                message=message,
            )
        except Exception as exc:
            raise InterventionResumeError(str(exc)) from exc

    @staticmethod
    def _intervention_expired(record: Any) -> bool:
        expires_at = getattr(record, "expires_at", None)
        if not expires_at:
            return False
        if isinstance(expires_at, str):
            try:
                parsed = datetime.fromisoformat(expires_at)
            except ValueError:
                return False
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed < datetime.now(UTC)
        try:
            return expires_at < datetime.now(UTC)
        except TypeError:
            return False

    async def _replay_outcome(
        self, event: dict[str, Any], run_id: str, surface: str
    ) -> WorkflowState:
        state = WorkflowState(run_id=run_id, event=event, surface=surface)
        status = await self._current_run_status(run_id)
        if status in {"completed", "delivered"}:
            return state.finish(WorkflowStatus.DELIVERED)
        return state.finish(WorkflowStatus.FAILED)

    async def _current_run_status(self, run_id: str) -> str | None:
        events = self.context.events
        if events is None:
            return None
        finder = getattr(events, "find_by_event_id", None)
        if finder is None:
            return None
        try:
            row = await finder(run_id)
            return str(row.get("status")) if isinstance(row, dict) else None
        except Exception:
            return None

    def _session_is_resumable(self, graph: Any, interrupt_id: str | None) -> bool:
        """Mirror Strands' own resume discriminator to preflight the resume.

        Strands only sets ``graph._resume_from_session`` and activates
        ``graph._interrupt_state`` *during* ``invoke``; they are never reliable
        before it runs. The same persisted payload it inspects there is what
        decides whether an interrupted run can be resumed: a multi-agent
        session that carries ``next_nodes_to_execute`` (a paused graph working
        through the review gate) can be resumed; a fresh/empty session cannot.
        """
        manager = getattr(graph, "session_manager", None)
        # A graph without any session manager does not restore state at all —
        # nothing for this guard to validate, so defer to Strands' behavior.
        if manager is None:
            return True
        if interrupt_id is None:
            return False
        if getattr(manager, "_is_new_session", True):
            return False
        try:
            state = manager.session_repository.read_multi_agent(manager.session_id, graph.id)
        except Exception:
            return False
        if not state:
            return False
        # An actual resume needs a non-empty, not-yet-finished node queue.
        next_nodes = state.get("next_nodes_to_execute") or []
        if not next_nodes:
            return False
        # When the persisted interrupt state is present, require the requested
        # interrupt to actually be among the ones the graph is waiting on.
        internal_state = state.get("_internal_state") or {}
        interrupt_state = (internal_state.get("interrupt_state") or {}).get("interrupts")
        return bool(interrupt_state is None or interrupt_id in interrupt_state)

    def _invocation_state(self, event: dict[str, Any], surface: str) -> dict[str, Any]:
        return {
            "run_id": str(event.get("event_id") or ""),
            "review_policy": event.get("review_policy") or self.context.review_policy(),
            "delivery_summary": "",
            "evaluation": {},
            "evidence_count": 0,
            "source": str(event.get("source") or "github"),
            "event_type": str(event.get("event_type") or "unknown"),
            "project_id": str(event.get("project_id") or ""),
            "surface": surface,
            "installation_id": event.get("installation_id"),
            "repo_dir": event.get("repo_dir"),
            "review_revision_of": event.get("review_revision_of"),
            "review_feedback": event.get("review_feedback"),
        }

    async def _notify_reviewers(self, run_id: str) -> None:
        """Best-effort Slack/Discord notifications on a pending-review transition.

        Failures are caught and logged without affecting run status.
        """
        notifier = getattr(self.context, "notifier", None)
        if notifier is None or getattr(notifier, "notify_reviewers", None) is None:
            logger.info("review_notify_skipped", run_id=run_id, reason="notifier_unavailable")
            return
        try:
            sent = await notifier.notify_reviewers(run_id)
        except Exception:
            logger.warning("review_notify_dispatch_failed", run_id=run_id, exc_info=True)
            return
        totals = {platform: len(recipients) for platform, recipients in (sent or {}).items()}
        total = sum(totals.values())
        if total == 0:
            logger.info("review_notify_skipped", run_id=run_id, reason="no_recipients")
            return
        logger.info(
            "review_notify_dispatched",
            run_id=run_id,
            total=total,
            slack_count=totals.get("slack", 0),
            discord_count=totals.get("discord", 0),
            email_count=totals.get("email", 0),
        )

    async def _drain_audit_flush(self, graph: Any, run_id: str) -> None:
        """Await the pending audit DB flush before the runner returns.

        The per-surface graph builders attach the ``RunAuditLogger`` instance
        as ``graph._draftly_audit_hook``; without it (test/downstream graphs)
        this is a no-op. Ensures agent_runs/agent_steps rows survive event-loop
        teardown instead of being cancelled with the run-end task.
        """
        hook = getattr(graph, "_draftly_audit_hook", None)
        if hook is None:
            return
        try:
            await hook.drain_run(run_id)
        except Exception:
            logger.warning("audit_drain_failed", run_id=run_id, exc_info=True)

    async def _finish_result(
        self,
        event: dict[str, Any],
        surface: str,
        result: Any,
        state: WorkflowState,
    ) -> WorkflowState:
        run_id = state.run_id
        state.result = result
        if result.status == Status.INTERRUPTED:
            steering_ids = await self._store_interrupts(run_id, surface, result, state)
            evaluation = self._node_payload(result, "evaluate")
            if steering_ids:
                return await self._finish_pending_intervention(
                    event, surface, state, evaluation, len(steering_ids)
                )
            pending_result: dict[str, Any] = {"status": "PENDING_REVIEW"}
            if evaluation:
                pending_result["evaluation"] = evaluation
            await self._persist_lifecycle(
                event,
                "pending_review",
                run_id=run_id,
                result=pending_result,
            )
            await self._mark(event, "pending_review")
            await self._broadcast_lifecycle(
                org_id=str(event.get("project_id") or ""),
                run_id=run_id,
                status="pending_review",
                surface=surface,
            )
            await self._notify_reviewers(run_id)
            return state.finish(WorkflowStatus.PENDING_REVIEW)

        if result.status == Status.COMPLETED:
            evaluation = self._node_payload(result, "evaluate")
            delivery_receipt = delivery_receipt_from_result(result)
            if is_blocked_delivery(delivery_receipt):
                # The graph completed but the delivery agent refused to act
                # (blocked). That is NOT a delivered outcome: surface it as
                # failed so the reviewer/notifier is not told the changes were
                # applied. Live run 7ddccdd0 surfaced "delivered" while the
                # receipt was blocked and no commit reached the PR.
                state.errors.append(
                    "delivery blocked: agent refused to deliver (no content)"
                )
                lifecycle_result: dict[str, Any] = {
                    "status": "FAILED",
                    "failed_nodes": ["deliver"],
                }
                if evaluation:
                    lifecycle_result["evaluation"] = evaluation
                await self._persist_lifecycle(
                    event,
                    "failed",
                    run_id=run_id,
                    error="; ".join(state.errors) or "delivery blocked",
                    result=lifecycle_result,
                )
                await self._persist_evaluation_outcome(event, run_id, evaluation)
                await self._mark(event, "failed")
                await self._broadcast_lifecycle(
                    org_id=str(event.get("project_id") or ""),
                    run_id=run_id,
                    status="failed",
                    surface=surface,
                )
                return state.finish(WorkflowStatus.FAILED)
            lifecycle_result = {"status": "COMPLETED"}
            if evaluation:
                lifecycle_result["evaluation"] = evaluation
            await self._persist_document_changes(event, run_id, result)
            delivered_receipt = await self._persist_delivery_result(event, run_id, result)
            if delivered_receipt is not None:
                await self._resolve_support_thread(state, delivered_receipt)
            await self._persist_lifecycle(
                event,
                "completed",
                run_id=run_id,
                result=lifecycle_result,
            )
            await self._persist_evaluation_outcome(event, run_id, evaluation)
            await self._mark(event, "completed")
            await _post_run_memory(self.context, state, surface)
            await self._broadcast_lifecycle(
                org_id=str(event.get("project_id") or ""),
                run_id=run_id,
                status="completed",
                surface=surface,
            )
            return state.finish(WorkflowStatus.DELIVERED)

        failed = self._failed_node_ids(result)
        state.errors.extend(failed)
        evaluation = self._node_payload(result, "evaluate")
        lifecycle_result = {"status": "FAILED", "failed_nodes": failed}
        if evaluation:
            lifecycle_result["evaluation"] = evaluation
        await self._persist_lifecycle(
            event,
            "failed",
            run_id=run_id,
            error="; ".join(failed) or "workflow failed",
            result=lifecycle_result,
        )
        await self._persist_evaluation_outcome(event, run_id, evaluation)
        await self._mark(event, "failed")
        await self._broadcast_lifecycle(
            org_id=str(event.get("project_id") or ""),
            run_id=run_id,
            status="failed",
            surface=surface,
        )
        return state.finish(WorkflowStatus.FAILED)

    async def _finish_pending_intervention(
        self,
        event: dict[str, Any],
        surface: str,
        state: WorkflowState,
        evaluation: dict[str, Any],
        n_interrupts: int,
    ) -> WorkflowState:
        """Persist the PENDING_INTERVENTION lifecycle for a paused steering run."""
        pending_result: dict[str, Any] = {"status": "PENDING_INTERVENTION"}
        if evaluation:
            pending_result["evaluation"] = evaluation
        await self._persist_lifecycle(
            event,
            "pending_intervention",
            run_id=state.run_id,
            result=pending_result,
        )
        await self._mark(event, "pending_intervention")
        await self._broadcast_lifecycle(
            org_id=str(event.get("project_id") or ""),
            run_id=state.run_id,
            status="pending_intervention",
            surface=surface,
        )
        _metrics.increment("draftly_run_pending_interventions_total")
        logger.info(
            "workflow_pending_intervention",
            run_id=state.run_id,
            n_interrupts=n_interrupts,
        )
        return state.finish(WorkflowStatus.PENDING_INTERVENTION)

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
        seq_allocator: _RunStreamSeq | None = getattr(graph, "_draftly_stream_seq", None)
        result: Any = None
        started_at = time.monotonic()
        ttft_recorded = False
        run_id = invocation_state["run_id"]
        try:
            async for raw in graph.stream_async(task, invocation_state=invocation_state):
                shaped = raw if isinstance(raw, dict) else {}
                envelope = filter_graph_event(
                    shaped,
                    run_id=run_id,
                    surface=surface,
                )
                if envelope is not None:
                    if not ttft_recorded and envelope.type == "text_delta":
                        ttft_recorded = True
                        _metrics.observe("draftly_run_ttft_ms", time.monotonic() - started_at)
                    seq = seq_allocator.next() if seq_allocator is not None else seq + 1
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
        except Exception as exc:
            node_id = _node_timeout_node_id(exc)
            if node_id is None:
                raise
            _metrics.increment("draftly_node_timeouts_total")
            logger.warning(
                "workflow_node_timeout",
                run_id=run_id,
                node_id=node_id,
                error=str(exc),
            )
            result = SimpleNamespace(
                status=Status.FAILED,
                interrupts=[],
                execution_order=[],
                failed_nodes=[SimpleNamespace(node_id=node_id)],
            )
            seq = seq_allocator.next() if seq_allocator is not None else seq + 1
            await self._safe_publish(
                StreamEnvelope(
                    type="workflow_result",
                    run_id=run_id,
                    surface=surface,
                    seq=seq,
                    node_id=node_id,
                    payload={
                        "status": "FAILED",
                        "reason": f"node {node_id} timed out",
                        "interrupts": [],
                    },
                )
            )
        if result is None:
            raise RuntimeError(f"stream ended without a result event run_id={run_id}")
        return result

    async def _safe_publish(self, envelope: StreamEnvelope) -> None:
        try:
            await self.publisher.publish(envelope)
        except Exception:
            logger.warning(
                "runner_publish_failed",
                run_id=envelope.run_id,
                seq=envelope.seq,
                exc_info=True,
            )

    async def _broadcast_lifecycle(
        self, *, org_id: str, run_id: str, status: str, surface: str
    ) -> None:
        broadcaster = getattr(getattr(self.context, "broadcaster", None), "broadcast", None)
        if broadcaster is None or not org_id:
            return
        try:
            await broadcaster(
                org_id,
                "workflow:changed",
                {"run_id": run_id, "status": status, "kind": surface},
            )
        except Exception:
            logger.warning(
                "runner_broadcast_failed run_id=%s status=%s",
                run_id,
                status,
                exc_info=True,
            )

    async def _record_routing_outcome(
        self,
        *,
        run_id: str,
        success: bool,
        latency_ms: float,
        decisions: dict[str, Any] | None = None,
        organization_id: str | None = None,
    ) -> None:
        """Best-effort telemetry: never fail the workflow over bookkeeping."""
        if decisions is None:
            decision = getattr(self.context, "routing_decision", None)
            decisions = {"workflow": decision} if decision is not None else {}
        if not decisions:
            return
        try:
            repositories = getattr(self.context, "repositories", None)
            if repositories is None:
                return
            for role, decision in decisions.items():
                # Telemetry aggregates are keyed by TASK type; profile is only
                # a display label (e.g. research tasks run on the reasoning profile).
                task_type = decision.task_type or decision.profile
                await repositories.routing.record(
                    {
                        "request_id": run_id,
                        "organization_id": organization_id,
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
                        "metadata": {"role": role},
                    }
                )
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

    @staticmethod
    def _node_payload(result: Any, node_id: str) -> dict[str, Any]:
        """Extract a custom node's JSON payload from a graph result."""
        for node in getattr(result, "execution_order", None) or []:
            if getattr(node, "node_id", None) != node_id:
                continue
            node_result = getattr(node, "result", None)
            nested = getattr(node_result, "result", None)
            results = getattr(nested, "results", None)
            if isinstance(results, dict) and node_id in results:
                node_result = getattr(results[node_id], "result", None)
            else:
                node_result = nested or node_result

            structured = getattr(node_result, "structured_output", None)
            if isinstance(structured, dict):
                return structured
            dump = getattr(structured, "model_dump", None)
            if callable(dump):
                value = dump()
                if isinstance(value, dict):
                    return value

            message = getattr(node_result, "message", None)
            content = message.get("content", []) if isinstance(message, dict) else []
            for block in content:
                if not isinstance(block, dict) or "text" not in block:
                    continue
                try:
                    value = json.loads(block["text"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    return value
        return {}

    # ========================================================
    # Persistence helpers (duck-typed repositories)
    # ========================================================

    async def _ensure_canonical_run(
        self,
        event: dict[str, Any],
        run_id: str,
        surface: str,
    ) -> None:
        repositories = getattr(self.context, "repositories", None)
        runs = getattr(repositories, "workflow_runs", None)
        if runs is None or not hasattr(runs, "start_or_get_idempotent"):
            return
        org_id = str(event.get("project_id") or "")
        if not org_id:
            logger.warning("canonical_run_skipped_without_org", run_id=run_id)
            return

        workflow_key = _workflow_key_for(event, surface)
        definitions = getattr(repositories, "workflow_definitions", None)
        definition = None
        if definitions is not None:
            requested_id = event.get("workflow_definition_id")
            if requested_id and hasattr(definitions, "get"):
                definition = await definitions.get(org_id=org_id, workflow_id=str(requested_id))
            if definition is None and hasattr(definitions, "find_active_by_key"):
                definition = await definitions.find_active_by_key(
                    org_id=org_id, workflow_key=workflow_key
                )
            if definition is None and hasattr(definitions, "create"):
                from draftly.app.api.workflow_schemas import WorkflowDefinitionCreate

                slug = f"default-{workflow_key.replace('_', '-')}"
                try:
                    definition = await definitions.create(
                        org_id=org_id,
                        created_by=None,
                        payload=WorkflowDefinitionCreate(
                            name=f"Default {workflow_key.replace('_', ' ').title()}",
                            slug=slug,
                            workflow_key=workflow_key,
                            status="active",
                            trigger_config={"source": str(event.get("source") or "webhook")},
                        ),
                    )
                except Exception:
                    # A concurrent first run may have created the same default.
                    if hasattr(definitions, "find_active_by_key"):
                        definition = await definitions.find_active_by_key(
                            org_id=org_id, workflow_key=workflow_key
                        )

        pr = event.get("pull_request") or {}
        repository = str(event.get("repository") or "") or None
        title = str(event.get("title") or pr.get("title") or "") or None
        target = {
            key: pr.get(key)
            for key in ("number", "html_url", "base", "head")
            if pr.get(key) is not None
        }
        await runs.start_or_get_idempotent(
            org_id=org_id,
            definition_id=str(definition.get("id")) if definition else None,
            source=str(event.get("source") or "github"),
            source_event_id=str(event.get("event_id") or run_id),
            title=title,
            metadata={
                "run_id": run_id,
                "event_type": str(event.get("event_type") or "unknown"),
                "repository": repository,
                "actor": str(event.get("actor") or "") or None,
                "target": target,
            },
        )

    @staticmethod
    def _safe_display_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not result:
            return None
        allowed = {"status", "evaluation", "failed_nodes", "error"}
        return {key: result[key] for key in allowed if key in result}

    async def _persist_lifecycle(
        self,
        event: dict[str, Any],
        status: str,
        *,
        run_id: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Keep the job and workflow identity rows in sync with execution."""
        repositories = getattr(self.context, "repositories", None)
        jobs = getattr(repositories, "jobs", None)
        if jobs is not None:
            try:
                await jobs.update_status(
                    job_id=run_id,
                    status=status,
                    error=error,
                    result=result,
                )
            except Exception:
                logger.exception("workflow_job_status_persist_failed", run_id=run_id, status=status)

        workflows = getattr(repositories, "github_workflows", None)
        if workflows is not None:
            try:
                await workflows.update_status(workflow_id=run_id, status=status)
            except Exception:
                logger.exception(
                    "github_workflow_status_persist_failed",
                    run_id=run_id,
                    status=status,
                )

        workflow_runs = getattr(repositories, "workflow_runs", None)
        if workflow_runs is not None and hasattr(workflow_runs, "update_state"):
            try:
                await workflow_runs.update_state(
                    org_id=str(event.get("project_id") or ""),
                    run_id=run_id,
                    status=status,
                    error=error,
                    output=self._safe_display_result(result),
                )
            except Exception:
                logger.exception(
                    "canonical_workflow_run_status_persist_failed",
                    run_id=run_id,
                    status=status,
                )

    async def _persist_evaluation_outcome(
        self,
        event: dict[str, Any],
        run_id: str,
        evaluation: dict[str, Any],
    ) -> None:
        """Write the live evaluation-gate verdict to the first-class stores.

        Mirrors the scheduled evaluation loop's persistence (evaluations
        table + feedback_outcomes) for the live PR path so gate telemetry is
        queryable per run instead of only embedded in jobs.result. Fail-open:
        a missing store must never fail the run.
        """
        if not evaluation:
            return
        org_id = str(event.get("project_id") or "") or None
        if not org_id:
            return
        repositories = getattr(self.context, "repositories", None)
        if repositories is None:
            return
        score = float(evaluation.get("score") or 0.0)
        passed = bool(evaluation.get("passed"))
        reasons = [str(r) for r in (evaluation.get("reasons") or [])]
        payload = {
            "passed": passed,
            "score": score,
            "reasons": reasons,
            "status": "passed" if passed else "failed",
        }

        outcomes = getattr(repositories, "feedback_outcomes", None)
        if outcomes is not None and getattr(outcomes, "save_outcome", None) is not None:
            try:
                await outcomes.save_outcome(org_id, "evaluation_gate", run_id, payload)
            except Exception:
                logger.exception("evaluation_outcome_persist_failed", run_id=run_id)

        evals = getattr(repositories, "evaluations", None)
        if evals is not None and getattr(evals, "create", None) is not None:
            try:
                now = datetime.now(UTC)
                await evals.create(
                    org_id=org_id,
                    evaluation_type="evaluation_gate",
                    run_id=run_id,
                    target_id=None,
                    score=round(score * 100.0, 2),
                    passed=passed,
                    status="passed" if passed else "failed",
                    metrics={"reasons": reasons},
                    failures=[{"reason": r} for r in reasons] if not passed else [],
                    trace_id=run_id,
                    started_at=now,
                    completed_at=now,
                )
            except Exception:
                logger.exception("evaluation_record_persist_failed", run_id=run_id)

    async def _persist_delivery_result(
        self,
        event: dict[str, Any],
        run_id: str,
        graph_result: Any,
    ) -> SupportDeliveryReceipt | None:
        """Persist a delivery receipt emitted by the delivery node.

        GitHub PR references go to the delivery repository; Slack/Discord
        replies write a ``SupportDeliveryReceipt`` to the platform workflow
        row (``delivered``/``failed``). Returns the persisted support receipt
        so the caller can resolve the support thread only after durability.
        """
        repositories = getattr(self.context, "repositories", None)

        receipt = delivery_receipt_from_result(graph_result)
        if not receipt:
            return None
        surface = str(receipt.get("surface") or "").lower()

        if surface in {"slack", "discord"}:
            return await self._persist_support_delivery(
                event, run_id, repositories, surface, receipt
            )

        delivery = getattr(repositories, "delivery", None)
        if delivery is None:
            return None
        if surface != "github":
            return None
        if str(receipt.get("status") or "completed").lower() != "completed":
            return None

        reference = str(receipt.get("reference") or "")
        number: int | None = None
        if "/pull/" in reference:
            try:
                number = int(reference.rsplit("/pull/", 1)[1].split("/", 1)[0])
            except ValueError:
                number = None
        elif reference.isdigit():
            number = int(reference)
        if number is None:
            return None

        repository = str(receipt.get("delivered_to") or event.get("repository") or "")
        owner, separator, name = repository.partition("/")
        if not separator or not owner or not name:
            return None
        try:
            await delivery.save_pull_request(
                PullRequestResult(
                    repository_id=repository,
                    owner=owner,
                    repository=name,
                    number=number,
                    url=reference if reference.startswith("http") else None,
                    title=str(receipt.get("title") or ""),
                    org_id=str(event.get("project_id") or "") or None,
                    run_id=run_id,
                )
            )
        except Exception:
            logger.exception("delivery_receipt_persist_failed", run_id=run_id)
        return None

    async def _resolve_support_thread(
        self, state: WorkflowState, receipt: SupportDeliveryReceipt
    ) -> None:
        """Mark a support thread resolved once its reply is durably persisted."""
        try:
            from draftly.workflows.support import resolve_support_thread

            await resolve_support_thread(self.context, state, receipt=receipt)
        except Exception:
            logger.exception(
                "support_thread_resolution_failed",
                run_id=state.run_id,
                receipt_id=receipt.provider_message_id,
            )

    async def _persist_support_delivery(
        self,
        event: dict[str, Any],
        run_id: str,
        repositories: Any,
        surface: str,
        receipt: dict[str, Any],
    ) -> SupportDeliveryReceipt | None:
        """Persist a Slack/Discord delivery receipt to the platform row."""
        platform_repo = getattr(
            repositories,
            "slack_workflows" if surface == "slack" else "discord_workflows",
            None,
        )
        if platform_repo is None:
            return None

        delivered = str(receipt.get("status") or "completed").lower() in {
            "completed",
            "delivered",
        }
        support_receipt = SupportDeliveryReceipt(
            run_id=run_id,
            org_id=str(event.get("project_id") or "") or None,
            platform=surface,
            channel_id=str(receipt.get("delivered_to") or event.get("channel") or "") or None,
            thread_id=str(event.get("thread_ts") or receipt.get("thread_id") or "") or None,
            source_message_id=str(event.get("source_message_id") or "") or None,
            provider_message_id=str(receipt.get("reference") or "") or None,
            status="delivered" if delivered else "failed",
            error=None if delivered else str(receipt.get("error") or ""),
        )
        try:
            await platform_repo.save_support_delivery(support_receipt)
        except Exception:
            logger.exception(
                "support_delivery_receipt_persist_failed",
                run_id=run_id,
                platform=surface,
            )
            return None
        return support_receipt

    async def _persist_document_changes(
        self,
        event: dict[str, Any],
        run_id: str,
        graph_result: Any,
    ) -> None:
        """Index concrete files from a completed documentation change plan."""
        documents = getattr(getattr(self.context, "repositories", None), "documents", None)
        if documents is None:
            return
        repository = str(event.get("repository") or "")
        for node in getattr(graph_result, "execution_order", []) or []:
            if str(getattr(node, "node_id", "")) not in {"update", "create"}:
                continue
            structured = getattr(getattr(node, "result", None), "structured_output", None)
            if hasattr(structured, "model_dump"):
                structured = structured.model_dump()
            if not isinstance(structured, dict):
                continue
            repository = str(structured.get("repository") or repository)
            for file in structured.get("files") or []:
                if (
                    not isinstance(file, dict)
                    or not file.get("path")
                    or not isinstance(file.get("content"), str)
                ):
                    continue
                try:
                    await documents.upsert(
                        org_id=str(event.get("project_id") or "") or None,
                        repository=repository,
                        path=str(file["path"]),
                        content=file["content"],
                        status="delivered",
                        metadata={"run_id": run_id, "source": "github_workflow"},
                    )
                except Exception:
                    logger.exception(
                        "document_persistence_failed",
                        run_id=run_id,
                        path=file.get("path"),
                    )

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
            logger.exception("runner_claim_failed", run_id=run_id)
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

    async def _existing_support_delivery(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Return an already-delivered receipt for a Slack/Discord source event."""
        source = str(event.get("source") or "")
        if source not in ("slack", "discord"):
            return None
        if not event.get("source_message_id"):
            return None
        repositories = getattr(self.context, "repositories", None)
        support = getattr(repositories, "support", None)
        finder = getattr(support, "get_support_delivery_by_source", None)
        if finder is not None:
            kwargs = {
                "org_id": str(event.get("project_id") or ""),
                "platform": source,
                "source_message_id": str(event.get("source_message_id") or ""),
            }
        else:
            platform_repo = getattr(
                repositories,
                "slack_workflows" if source == "slack" else "discord_workflows",
                None,
            )
            finder = getattr(platform_repo, "get_support_delivery_by_source", None)
            kwargs = {
                "org_id": str(event.get("project_id") or ""),
                "source_message_id": str(event.get("source_message_id") or ""),
            }
        if finder is None:
            return None
        try:
            return await finder(**kwargs)
        except Exception:
            logger.exception(
                "support_delivery_lookup_failed",
                run_id=event.get("event_id"),
                source=source,
            )
            return None

    async def _mark(self, event: dict[str, Any], status: str) -> None:
        events = self.context.events
        if events is None:
            return
        marker = getattr(events, "mark_status", None)
        if marker is None:
            return
        try:
            await marker(str(event.get("event_id")), status)
        except Exception:
            logger.warning(
                "event_status_persist_failed",
                event_id=str(event.get("event_id")),
                status=status,
                exc_info=True,
            )

    async def _store_interrupts(
        self,
        run_id: str,
        surface: str,
        result: Any,
        state: WorkflowState,
    ) -> set[str]:
        """Record interrupts on the state; return the steering interrupt ids.

        Steering interrupts are already durable (``workflow_interventions``)
        so they must not also enter the human-review inbox. Review-gate
        interrupts keep their existing review persistence.
        """
        reviews = self.context.reviews
        interventions = getattr(
            getattr(self.context, "repositories", None), "steering_interventions", None
        )
        org_id = str(state.event.get("project_id") or "")
        steering_ids: set[str] = set()
        for interrupt in result.interrupts or []:
            is_steering = False
            if interventions is not None:
                try:
                    pending = await interventions.get_pending(
                        run_id=run_id, interrupt_id=interrupt.id, org_id=org_id
                    )
                    is_steering = pending is not None
                except Exception:
                    is_steering = False
            if is_steering:
                steering_ids.add(interrupt.id)
            reason = await self._enrich_review_reason(interrupt.reason, state)
            record = {
                "interrupt_id": interrupt.id,
                "reason": reason,
            }
            state.interrupts.append(record)
            if reviews is None or is_steering:
                continue
            try:
                await reviews.store_interrupt(
                    run_id=run_id,
                    interrupt_id=interrupt.id,
                    reason=reason,
                    workflow_type=surface,
                    org_id=org_id,
                )
            except Exception:
                logger.exception("store_interrupt_failed", run_id=run_id)
        return steering_ids

    async def _enrich_review_reason(
        self,
        reason: Any,
        state: WorkflowState,
    ) -> dict[str, Any]:
        """Add organization-scoped original document bodies before persistence."""
        if not isinstance(reason, dict):
            return {}
        enriched = deepcopy(reason)
        document = enriched.get("document")
        if not isinstance(document, dict):
            return enriched
        files = document.get("files")
        if not isinstance(files, list):
            return enriched
        repository = document.get("repository")
        documents = getattr(getattr(self.context, "repositories", None), "documents", None)
        org_id = str(state.event.get("project_id") or "")
        for file in files:
            if not isinstance(file, dict):
                continue
            action = str(file.get("action") or "").lower()
            if action not in {"update", "create"}:
                continue
            file["original_content"] = None
            file["original_content_available"] = False
            if action == "create" or not repository or not file.get("path") or documents is None:
                continue
            try:
                existing = await documents.get_by_org_repository_path(
                    org_id=org_id,
                    repository=str(repository),
                    path=str(file["path"]),
                )
            except Exception:
                logger.warning(
                    "review_original_content_lookup_failed",
                    org_id=org_id,
                    repository=repository,
                    path=file.get("path"),
                    exc_info=True,
                )
                continue
            content = existing.get("content") if isinstance(existing, dict) else None
            if isinstance(content, str):
                file["original_content"] = content
                file["original_content_available"] = True
        return enriched
