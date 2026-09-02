"""Documentation evaluation loop workflow (plan §7.2).

Scheduled (or API-triggered): run the deterministic evaluation graph over
golden datasets, stream stage/progress events over the workflow event bus,
and persist the summary to the evaluations repository. Publish org-scoped
``workflow:changed`` / ``evaluation:created`` frames to the dashboard feed.
"""

from __future__ import annotations

import asyncio
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


async def run_evaluation_loop(
    context: WorkflowContext,
    *,
    org_id: str = "",
    run_id: str | None = None,
    datasets: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> WorkflowState:
    """Run the evaluation graph; stream progress; persist a summary."""
    del kwargs
    state = WorkflowState(run_id=run_id or f"evaluation-{uuid4()}")
    seq = 0
    started_at = datetime.now(UTC)

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
                job_id=state.run_id,
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

    await _publish("stage_change", {
        "stage": "load_datasets", "status": "completed",
        "stats": {"datasets": len(datasets)},
    })
    await _publish("stage_progress", {"stage": "load_datasets", "progress": 100})

    await _publish("stage_change", {"stage": "run_experiments", "status": "started"})
    await _publish("stage_progress", {"stage": "run_experiments", "progress": 10})

    from draftly.orchestration.graphs.evaluation_graph import (
        build_evaluation_graph,
    )

    graph = build_evaluation_graph()
    result = await _run_graph(graph, state, datasets)

    await _publish("stage_progress", {"stage": "run_experiments", "progress": 90})

    state.result = result

    if result.status != Status.COMPLETED:
        state.errors.append(f"evaluation graph ended {result.status}")
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
    persisted = await _persist_summary(context, state, org_id=org_id, started_at=started_at)

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
) -> dict[str, Any] | None:
    """Best-effort persistence of the run summary to the evaluations table."""
    try:
        from draftly.orchestration.nodes.base import node_data

        summary = getattr(state.result, "state", None)
        data = {}
        if summary is not None:
            persist_node = summary.results.get("persist") if hasattr(summary, "results") else None
            data = node_data(summary, "persist") if persist_node else {}
    except Exception:
        data = {}
    if not data:
        data = {"total": 0, "passed": 0, "failed": 0, "passed_all": False, "errors": []}

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
        )
    except Exception:
        logger.exception("evaluation_loop_persist_failed")
        return None
