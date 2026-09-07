"""GitHub release workflow (plan §7.2).

A published release triggers the documentation graph against the release
diff surface (same graph, release event type).
"""

from __future__ import annotations

from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)


async def _set_job_status(context: WorkflowContext, run_id: str, status: str) -> None:
    jobs = getattr(getattr(context, "repositories", None), "jobs", None)
    if jobs is None or not run_id:
        return
    try:
        await jobs.update_status(job_id=run_id, status=status)
    except Exception:
        logger.warning(
            "release_job_status_failed",
            run_id=run_id,
            status=status,
            exc_info=True,
        )


async def run_release_workflow(
    context: WorkflowContext,
    event: dict[str, Any],
    run_id: str | None = None,
) -> WorkflowState:
    """Run the documentation graph for one release event."""
    resolved_run_id = run_id or str(event.get("event_id") or "")
    await _set_job_status(context, resolved_run_id, "running")
    runner = WorkflowRunner(context, publisher=context.publisher)
    state = await runner.run(event)
    if state.status == WorkflowStatus.FAILED:
        await _set_job_status(context, resolved_run_id, "failed")
    elif state.status == WorkflowStatus.DELIVERED:
        await _set_job_status(context, resolved_run_id, "completed")
    log = logger.error if state.status == WorkflowStatus.FAILED else logger.info
    log("release_workflow_done", run_id=state.run_id, status=state.status.value)
    return state
