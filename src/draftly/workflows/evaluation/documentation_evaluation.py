"""Documentation evaluation loop workflow (plan §7.2).

Scheduled: run the deterministic evaluation graph over golden datasets
and persist the summary to the evaluations repository.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from strands.multiagent.base import Status

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


async def run_evaluation_loop(
    context: WorkflowContext,
    *,
    datasets: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> WorkflowState:
    """Run the evaluation graph; persist a summary when possible."""
    del kwargs
    state = WorkflowState(run_id=f"evaluation-{id(object())}")

    if datasets is None:
        datasets = await _load_datasets(context)

    from draftly.orchestration.graphs.evaluation_graph import (
        build_evaluation_graph,
    )

    graph = build_evaluation_graph()
    result = await graph.invoke_async(
        "{}",
        invocation_state={"run_id": state.run_id, "datasets": datasets},
    )
    state.result = result

    if result.status != Status.COMPLETED:
        state.errors.append(f"evaluation graph ended {result.status}")
        return state.finish(WorkflowStatus.FAILED)

    await _persist_summary(context, state)
    return state.finish(WorkflowStatus.DELIVERED)


async def _load_datasets(context: WorkflowContext) -> list[dict[str, Any]]:
    """Load golden datasets from the evaluations repository."""
    evaluations = getattr(context.repositories, "evaluations", None) if context else None
    loader = getattr(evaluations, "list_datasets", None) if evaluations else None
    if loader is None:
        return []
    try:
        return list(await loader())
    except Exception:
        logger.exception("evaluation_loop_load_datasets_failed")
        return []


async def _persist_summary(context: WorkflowContext, state: WorkflowState) -> None:
    """Best-effort persistence of the run summary."""
    from draftly.orchestration.nodes.base import node_data

    graph_result = state.result
    summary = getattr(graph_result, "state", None)
    try:
        persist_node = summary.results.get("persist") if summary else None
        data = node_data(summary, "persist") if persist_node else {}
    except Exception:
        data = {}

    repository = getattr(context.repositories, "evaluations", None) if context else None
    saver = getattr(repository, "save_run_summary", None) if repository else None
    if saver is None:
        logger.info(
            "evaluation_loop_summary run_id=%s total=%s passed=%s",
            state.run_id,
            data.get("total"),
            data.get("passed"),
        )
        return
    try:
        await saver(json.dumps(data))
    except Exception:
        logger.exception("evaluation_loop_persist_failed")
