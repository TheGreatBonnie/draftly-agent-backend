"""Documentation evaluation loop workflow (plan §7.2).

Scheduled (or API-triggered): run the deterministic evaluation graph over
golden datasets, stream stage/progress events over the workflow event bus,
and persist the summary to the evaluations repository. Publish org-scoped
``workflow:changed`` / ``evaluation:created`` frames to the dashboard feed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from strands.multiagent.base import Status

from draftly.events.stream_envelope import StreamEnvelope
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)

STAGE_LABELS = {
    "load_datasets": "Load golden datasets",
    "run_experiments": "Run evaluation graph",
    "persist_results": "Persist summary",
}

# Dataset ``surface`` -> canonical ``evaluation_type`` persisted to the
# evaluations table. This is what lets the dashboard/API distinguish a GitHub
# issue evaluation (``github_issue``) from a docs/PR evaluation
# (``documentation``) or a support evaluation (``support``). A run may span
# multiple datasets with different surfaces, so the type reflects the
# (deduped) set of surfaces; the primary is the first dataset's surface.
SURFACE_TO_EVALUATION_TYPE = {
    "pull_request": "documentation",
    "documentation": "documentation",
    "issue": "github_issue",
    "support": "support",
}


def _evaluation_types(datasets: list[dict[str, Any]] | None) -> list[str]:
    """Canonical evaluation types for a run, from its datasets' surfaces."""
    seen: list[str] = []
    for ds in datasets or []:
        surface = (ds or {}).get("surface")
        if not surface:
            continue
        etype = SURFACE_TO_EVALUATION_TYPE.get(surface, surface)
        if etype not in seen:
            seen.append(etype)
    return seen


async def run_evaluation_loop(
    context: WorkflowContext,
    *,
    org_id: str = "org_3IfMDevV4Tg8DLD8Ljc0GG6c2GJ",
    run_id: str | None = None,
    datasets: list[dict[str, Any]] | None = None,
    live: bool = False,
    **kwargs: Any,
) -> WorkflowState:
    """Run the evaluation graph; stream progress; persist a summary.

    Args:
        live: If True, enables live agent invocation with LLM-judge evaluators
            (requires model keys in settings).
    """
    state = WorkflowState(run_id=run_id or f"evaluation-{uuid4()}")
    seq = 0
    started_at = datetime.now(UTC)

    logger.info(
        "evaluation_loop_started",
        run_id=state.run_id,
        org_id=org_id,
        live=live,
        datasets_provided=datasets is not None,
    )

    async def _publish(envelope_type: str, payload: dict[str, Any]) -> None:
        nonlocal seq
        if context.publisher is None:
            return
        seq += 1
        await context.publisher.publish(
            StreamEnvelope(
                type=envelope_type,
                run_id=state.run_id,
                surface="evaluation",
                seq=seq,
                payload=payload,
            )
        )
        await asyncio.sleep(0)

    async def _broadcast(event_type: str, payload: dict[str, Any]) -> None:
        broadcaster = getattr(context, "broadcaster", None)
        if broadcaster is None:
            return
        try:
            await broadcaster.broadcast(org_id, event_type, payload)
        except Exception:
            logger.warning(
                "evaluation_broadcast_failed run=%s type=%s",
                state.run_id,
                event_type,
                exc_info=True,
            )

    async def _set_job_status(status: str) -> None:
        jobs = getattr(getattr(context, "repositories", None), "jobs", None)
        if jobs is None:
            return
        try:
            await jobs.update_status(job_id=state.run_id, status=status)
        except Exception:
            logger.warning(
                "evaluation_job_status_failed run=%s status=%s",
                state.run_id,
                status,
                exc_info=True,
            )

    await _publish("stage_change", {"stage": "evaluation_started", "status": "started"})
    await _publish("stage_manifest", {
        "stages": [
            {"id": s, "label": STAGE_LABELS[s], "order": i}
            for i, s in enumerate(STAGE_LABELS)
        ]
    })

    # Ensure a jobs row exists so /stream-ticket resolves this run.
    jobs = getattr(getattr(context, "repositories", None), "jobs", None)
    if jobs is not None:
        try:
            await jobs.insert(
                # NOTE: do NOT pass `job_id=` here. `jobs.id` is a UUID column
                # (migration 013) with a `DEFAULT gen_random_uuid()`; passing the
                # prefixed run_id string ("evaluation-<uuid>", 47 chars) into it
                # raises a Postgres `DataError` that aborts the insert so no row
                # is ever written. The row is then keyed by `run_id`, which is a
                # TEXT column (migration 036) and safely accepts the prefixed run_id.
                # Leaving `job_id` to default generates a valid UUID `id` server-side
                # and lets `_set_job_status`/`update_status` (which filters by run_id)
                # actually find the row.
                run_id=state.run_id,
                org_id=org_id,
                name="Documentation evaluation",
                job_type="evaluation",
                schedule="",
                configuration={},
                status="running",
            )
            logger.info("evaluation_job_row run=%s org=%s", state.run_id, org_id)
        except Exception:
            logger.warning("evaluation_job_root_failed run=%s", state.run_id, exc_info=True)
    await _broadcast(
        "workflow:changed",
        {"run_id": state.run_id, "status": "running", "kind": "evaluation"},
    )

    if datasets is None:
        datasets = await _load_datasets(context)

    logger.info(
        "evaluation_loop_datasets_loaded",
        run_id=state.run_id,
        datasets=[d.get("name") for d in datasets],
    )

    await _publish("stage_change", {
        "stage": "load_datasets", "status": "completed",
        "stats": {"datasets": len(datasets)},
    })
    await _publish("stage_progress", {"stage": "load_datasets", "progress": 100})

    await _publish("stage_change", {"stage": "run_experiments", "status": "started"})
    await _publish("stage_progress", {"stage": "run_experiments", "progress": 10})

    from draftly.integrations.strands.models import resolve_concrete_model
    from draftly.orchestration.graphs.evaluation_graph import (
        build_evaluation_graph,
    )

    judge_model = None
    if live:
        try:
            judge_model = resolve_concrete_model()
            logger.info(
                "evaluation_live_judge_model_resolved",
                run_id=state.run_id,
            )
        except Exception as e:
            logger.warning("live_mode_model_unavailable: %s", e)

    graph = build_evaluation_graph(
        tools_registry=getattr(context, "tools", None),
        model=getattr(context, "model", None),
        judge_model=judge_model if live else None,
        # Live runs invoke real agents (deep context/research/impact/writer)
        # PLUS streamed LLM judges, which cannot fit the sync 600s default.
        # Give each dataset enough headroom to finish and be scored.
        dataset_timeout=1800.0 if live else None,
        # Node-level tool requirements must come from each dataset's
        # ``required_tools`` field (single source of truth), not a hardcoded
        # map here — a hardcoded map previously left stale impact expectations
        # (read_file/git_diff) that no longer match the bundled scenarios.
        surface_required_tools=None,
        run_id_prefix=state.run_id,
    )
    result = await _run_graph(graph, state, datasets)

    await _publish("stage_progress", {"stage": "run_experiments", "progress": 90})

    state.result = result

    if result.status != Status.COMPLETED:
        state.errors.append(f"evaluation graph ended {result.status}")
        logger.warning(
            "evaluation_loop_failed",
            run_id=state.run_id,
            status=result.status,
            error=state.errors[-1],
        )
        await _publish("stage_change", {"stage": "run_experiments", "status": "failed"})
        await _publish("workflow_result", {"status": "FAILED", "error": state.errors[-1]})
        await _set_job_status("failed")
        await _broadcast(
            "workflow:changed",
            {"run_id": state.run_id, "status": "failed", "kind": "evaluation"},
        )
        return state.finish(WorkflowStatus.FAILED)

    await _publish("stage_change", {"stage": "run_experiments", "status": "completed"})
    await _publish("stage_progress", {"stage": "run_experiments", "progress": 100})

    await _publish("stage_change", {"stage": "persist_results", "status": "started"})
    persisted = await _persist_summary(
        context,
        state,
        org_id=org_id,
        started_at=started_at,
        datasets=datasets,
    )

    await _publish("stage_change", {"stage": "persist_results", "status": "completed"})
    await _publish("overall_progress", {"progress": 100})
    await _publish("workflow_result", {"status": "DELIVERED"})

    await _set_job_status("completed")
    await _broadcast(
        "workflow:changed",
        {"run_id": state.run_id, "status": "completed", "kind": "evaluation"},
    )
    if persisted is not None:
        await _broadcast("evaluation:created", {
            "run_id": state.run_id,
            "evaluation_id": str(persisted.get("id") or ""),
            "status": str(persisted.get("status") or "completed"),
        })

    logger.info(
        "evaluation_loop_completed",
        run_id=state.run_id,
        persisted=persisted is not None,
        errors=len(state.errors),
    )

    return state.finish(WorkflowStatus.DELIVERED)


async def _run_graph(graph: Any, state: WorkflowState, datasets: list[dict[str, Any]]) -> Any:
    """Invoke the evaluation graph with the loaded datasets."""
    return await graph.invoke_async(
        "{}",
        invocation_state={"run_id": state.run_id, "datasets": datasets},
    )


async def _load_datasets(context: WorkflowContext) -> list[dict[str, Any]]:
    """Load golden datasets from the evaluations repository."""
    evaluations = getattr(getattr(context, "repositories", None), "evaluations", None)
    loader = getattr(evaluations, "list_datasets", None) if evaluations else None
    if loader is None:
        return []
    try:
        return list(loader())
    except Exception:
        logger.exception("evaluation_loop_load_datasets_failed")
        return []


async def _persist_summary(
    context: WorkflowContext,
    state: WorkflowState,
    *,
    org_id: str,
    started_at: datetime,
    datasets: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Best-effort persistence of the run summary to the evaluations table."""
    data = _graph_node_payload(state.result, "persist") or {}
    if not data:
        data = {"total": 0, "passed": 0, "failed": 0, "passed_all": False, "errors": []}
    # Carry the granular (per-case, per-metric) rows from the run node so the
    # repository can persist more than a single coarse summary row.
    rows = _graph_node_payload(state.result, "run") or {}
    data["rows"] = rows.get("results", [])

    # Evaluation type from the datasets' surfaces (e.g. github_issue, support,
    # documentation) so the row isn't mislabeled as "documentation" when a
    # github issue dataset runs. Fall back to "documentation" for callers that
    # don't supply any dataset surface metadata (backward compatibility).
    types = _evaluation_types(datasets)
    evaluation_type = types[0] if types else "documentation"
    data["evaluation_types"] = types

    repository = getattr(getattr(context, "repositories", None), "evaluations", None)
    saver = getattr(repository, "save_run_summary", None) if repository else None
    if saver is None:
        logger.info(
            "evaluation_loop_summary run_id=%s total=%s passed=%s",
            state.run_id,
            data.get("total"),
            data.get("passed"),
        )
        return None
    try:
        completed_at = datetime.now(UTC)
        return await saver(
            summary=data,
            org_id=org_id,
            run_id=state.run_id,
            started_at=started_at,
            completed_at=completed_at,
            evaluation_type=evaluation_type,
        )
    except Exception:
        logger.exception("evaluation_loop_persist_failed")
        return None


def _graph_node_payload(result: Any, node_id: str) -> dict[str, Any]:
    """Extract a custom node's structured payload from a graph result.

    ``graph.invoke_async`` returns a ``GraphResult`` (a ``MultiAgentResult``
    subclass) whose per-node outputs hang off ``execution_order`` entries
    (``GraphNode.node_id`` / ``GraphNode.result``), NOT off a ``.state`` or
    ``.results`` attribute. Our custom nodes return
    ``MultiAgentResult(results={node_id: NodeResult(result=agent_result(...))})``,
    so the payload lives at ``GraphNode.result.result.results[node_id].result``
    as an ``AgentResult`` whose message text carries the JSON.

    Returns {} when the node never ran or the payload cannot be parsed.
    """
    try:
        from strands.agent.agent_result import AgentResult
        from strands.multiagent.base import MultiAgentResult

        for node in getattr(result, "execution_order", None) or []:
            if getattr(node, "node_id", None) != node_id:
                continue
            inner = getattr(getattr(node, "result", None), "result", None)
            if isinstance(inner, MultiAgentResult):
                nested = (inner.results or {}).get(node_id)
                if nested is not None:
                    inner = getattr(nested, "result", None)
            if not isinstance(inner, AgentResult):
                continue
            structured = getattr(inner, "structured_output", None)
            if structured is not None:
                dump = getattr(structured, "model_dump", None)
                if callable(dump):
                    return dict(dump())
            content = (
                (inner.message or {}).get("content", [])
                if isinstance(inner.message, dict)
                else []
            )
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    return json.loads(block["text"])
    except Exception:  # noqa: BLE001 - best-effort persistence
        logger.warning("evaluation_graph_node_payload_failed node=%s", node_id, exc_info=True)
    return {}
