"""Status reconciliation between the events row (source of truth) and the
jobs / github_workflows read-model rows.

Heals the fanned-out status skew on the PR path: an events terminal write
that succeeded while the jobs write failed, or a replayed event whose
original run already finished. Idempotent and best-effort by design.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext

logger = structlog.get_logger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "failed", "pending_review", "skipped"})

DEFAULT_STALE_AFTER_SECONDS = 3600


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


def _default_stale_after(context: WorkflowContext) -> int:
    """Sweep threshold: strand execution_timeout + margin, else 3600s."""
    config = getattr(context, "config", None)
    strands = getattr(config, "strands", None)
    timeout = getattr(strands, "execution_timeout", None)
    if isinstance(timeout, (int, float)) and timeout > 0:
        return max(DEFAULT_STALE_AFTER_SECONDS, int(timeout) + 600)
    return DEFAULT_STALE_AFTER_SECONDS


async def _set_read_models_failed(context: WorkflowContext, run_id: str) -> None:
    """Direct read-model alignment when no events row exists for the run."""
    repositories = getattr(context, "repositories", None)
    jobs = getattr(repositories, "jobs", None)
    if jobs is not None and getattr(jobs, "update_status", None) is not None:
        try:
            await jobs.update_status(job_id=run_id, status="failed")
        except Exception:
            logger.warning("sweep_job_status_failed", run_id=run_id, exc_info=True)
    workflows = getattr(repositories, "github_workflows", None)
    if workflows is not None and getattr(workflows, "update_status", None) is not None:
        try:
            await workflows.update_status(workflow_id=run_id, status="failed")
        except Exception:
            logger.warning("sweep_workflow_status_failed", run_id=run_id, exc_info=True)


async def mark_stale_runs_failed(
    context: WorkflowContext,
    *,
    stale_after_seconds: int | None = None,
    limit: int = 200,
) -> int:
    """Fail runs stuck in ``running`` past the threshold (best-effort recovery).

    A run is stuck when its jobs row is still ``running`` past the threshold
    AND the ``workflow_events`` log has no terminal ``workflow_result``
    envelope — meaning ``_finish_result`` never ran (worker death, wedged loop,
    sync-blocked node). The events row is marked failed first (source of
    truth), then ``reconcile_run()`` aligns the read models, then the
    dashboard is pushed a refresh. Idempotent: rerunning is a no-op once rows
    are terminal. Never raises.
    """
    repositories = getattr(context, "repositories", None)
    jobs = getattr(repositories, "jobs", None)
    lister = getattr(jobs, "list_stuck", None)
    if jobs is None or lister is None:
        logger.info("stale_sweep_skipped reason=no_jobs_lister")
        return 0

    stale_after = stale_after_seconds or _default_stale_after(context)
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after)
    rows = await lister(started_before=cutoff, status="running", limit=limit)

    run_ids = [str(r.get("run_id") or "") for r in rows if r.get("run_id")]
    if not run_ids:
        return 0

    work_events = getattr(repositories, "workflow_events", None)
    terminal_selector = getattr(work_events, "terminal_run_ids", None) if work_events is not None else None
    terminal: set[str] = set()
    if terminal_selector is not None:
        try:
            terminal = set(await terminal_selector(run_ids))
        except Exception:
            logger.warning("stale_sweep_terminal_lookup_failed", exc_info=True)

    events = getattr(repositories, "events", None)
    events_finder = getattr(events, "find_by_event_id", None)
    events_marker = getattr(events, "mark_status", None)
    broadcaster = getattr(getattr(context, "broadcaster", None), "broadcast", None)

    recovered = 0
    for row in rows:
        run_id = str(row.get("run_id") or "")
        if not run_id or run_id in terminal:
            continue
        # Re-check right before marking: the runner persists the terminal
        # workflow_result envelope BEFORE marking anything terminal, so this
        # closes the race where a run finished while the sweep ran.
        if terminal_selector is not None:
            try:
                if run_id in await terminal_selector([run_id]):
                    continue
            except Exception:
                logger.warning("stale_sweep_terminal_recheck_failed", run_id=run_id, exc_info=True)

        try:
            if events_finder is not None and events_marker is not None:
                existing = await events_finder(run_id)
                if isinstance(existing, dict):
                    await events_marker(run_id, "failed")
        except Exception:
            logger.warning("stale_sweep_event_mark_failed", run_id=run_id, exc_info=True)

        # events row now reads terminal 'failed' → reconcile_run aligns read
        # models; when no events row exists it returns None and we align directly.
        if await reconcile_run(context, run_id) is None:
            await _set_read_models_failed(context, run_id)

        org_id = str(row.get("org_id") or "")
        if broadcaster is not None and org_id:
            try:
                await broadcaster(
                    org_id, "workflow:changed",
                    {"run_id": run_id, "status": "failed", "kind": "sweep"},
                )
            except Exception:
                logger.warning("stale_sweep_broadcast_failed", run_id=run_id, exc_info=True)

        logger.info("stale_run_marked_failed", run_id=run_id, stale_after_seconds=stale_after)
        recovered += 1

    logger.info("stale_sweep_done", scanned=len(rows), recovered=recovered)
    return recovered


async def reconcile_stale_on_boot(application: Any) -> int:
    """Run the sweep at RQ worker boot using the composed context."""
    workflows = getattr(application, "workflows", None)
    context = getattr(workflows, "context", None)
    if context is None:
        return 0
    return await mark_stale_runs_failed(context)
