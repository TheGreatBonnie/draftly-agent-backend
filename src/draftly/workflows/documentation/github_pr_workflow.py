"""GitHub pull-request workflow (plan §7.2).

Thin adapter: normalized PR event → WorkflowRunner (documentation graph).
The runner owns idempotency, graph build, invocation, and outcomes.
When context.publisher is set, the runner streams graph envelopes and a
terminal workflow_result over the per-run channel. Job status is mirrored
to the jobs table (running/completed/failed) so the run is visible to
/workflows/{run_id}/events.
"""

from __future__ import annotations

from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)


def _resolve_run_id(event: dict[str, Any], run_id: str | None) -> str:
    return run_id or str(event.get("event_id") or "")


async def _set_job_status(context: WorkflowContext, run_id: str, status: str) -> None:
    """Best-effort: persist job status; never fails the workflow."""
    jobs = getattr(getattr(context, "repositories", None), "jobs", None)
    if jobs is None or not run_id:
        return
    try:
        await jobs.update_status(job_id=run_id, status=status)
    except Exception:
        logger.warning(
            "pr_job_status_failed",
            run_id=run_id,
            status=status,
            exc_info=True,
        )


async def run_pull_request_workflow(
    context: WorkflowContext,
    event: dict[str, Any],
    run_id: str | None = None,
) -> WorkflowState:
    """Run the documentation graph for one pull-request event."""
    run_id = _resolve_run_id(event, run_id)
    await _set_job_status(context, run_id, "running")

    runner = WorkflowRunner(context, publisher=context.publisher)
    state = await runner.run(event)

    if state.status == WorkflowStatus.FAILED:
        await _set_job_status(context, run_id, "failed")
    elif state.status in (WorkflowStatus.SKIPPED, WorkflowStatus.DUPLICATE):
        # A skip/duplicate (e.g. non-merged PR replayed or idempotency duplicate)
        # is not an error; leave the row as pending/running rather than marking
        # completed/failed so /workflows/{run_id}/events replay stays coherent.
        pass
    elif state.status == WorkflowStatus.PENDING_REVIEW:
        # Intermediate — human review gate, not terminal completion.
        pass
    elif state.status == WorkflowStatus.DELIVERED:
        await _set_job_status(context, run_id, "completed")
    else:
        # Fallback for any future status: only DELIVERED is success, FAILED is
        # failure; other unknowns should not mark completed.
        logger.warning(
            "pr_workflow_unknown_status",
            run_id=run_id,
            status=state.status.value,
        )

    log = logger.error if state.status == WorkflowStatus.FAILED else logger.info
    log("pr_workflow_done", run_id=state.run_id, status=state.status.value)
    return state
