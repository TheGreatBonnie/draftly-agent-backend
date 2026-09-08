"""Status reconciliation between the events row (source of truth) and the
jobs / github_workflows read-model rows.

Heals the fanned-out status skew on the PR path: an events terminal write
that succeeded while the jobs write failed, or a replayed event whose
original run already finished. Idempotent and best-effort by design.
"""

from __future__ import annotations

import structlog

from draftly.workflows.context import WorkflowContext

logger = structlog.get_logger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "failed", "pending_review", "skipped"})


async def reconcile_run(context: WorkflowContext, run_id: str) -> str | None:
    """Align jobs + github_workflows to the events row's terminal status.

    Returns the reconciled status, or ``None`` when there is nothing to
    align (no events row, or the event is still in-flight). Never raises.
    """
    if not run_id:
        return None
    events = getattr(context, "events", None)
    finder = getattr(events, "find_by_event_id", None)
    if events is None or finder is None:
        return None
    row = await finder(run_id)
    if not isinstance(row, dict):
        return None
    status = str(row.get("status") or "")
    if status not in TERMINAL_STATUSES:
        return None
    repositories = getattr(context, "repositories", None)

    jobs = getattr(repositories, "jobs", None)
    job_updater = getattr(jobs, "update_status", None)
    if jobs is not None and job_updater is not None:
        try:
            await job_updater(job_id=run_id, status=status)
        except Exception:
            logger.warning(
                "reconcile_job_status_failed",
                run_id=run_id,
                status=status,
                exc_info=True,
            )

    workflows = getattr(repositories, "github_workflows", None)
    workflow_updater = getattr(workflows, "update_status", None)
    if workflows is not None and workflow_updater is not None:
        try:
            await workflow_updater(workflow_id=run_id, status=status)
        except Exception:
            logger.warning(
                "reconcile_workflow_status_failed",
                run_id=run_id,
                status=status,
                exc_info=True,
            )

    return status


async def reconcile_stale_runs(context: WorkflowContext, *, limit: int = 200) -> int:
    """Sweep recent events and align their read-model rows to terminal status.

    Returns the number of runs reconciled. Best-effort; individual failures
    are logged and skipped.
    """
    events = getattr(context, "events", None)
    lister = getattr(events, "list_recent_runs", None)
    if events is None or lister is None:
        return 0
    rows = await lister(limit=limit)
    reconciled = 0
    for row in rows or []:
        run_id = str((row or {}).get("event_id") or "")
        if await reconcile_run(context, run_id) is not None:
            reconciled += 1
    return reconciled
