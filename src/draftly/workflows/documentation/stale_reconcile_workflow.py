"""Scheduled stale-run recovery workflow (entry point for rq-scheduler)."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext
from draftly.workflows.documentation.reconciliation import mark_stale_runs_failed

logger = structlog.get_logger(__name__)


async def run_stale_reconcile(context: WorkflowContext, **kwargs: Any) -> int:
    """Fail runs stuck in ``running``; returns how many were recovered."""
    raw = kwargs.get("stale_after_seconds")
    stale_after = int(raw) if raw else None
    recovered = await mark_stale_runs_failed(context, stale_after_seconds=stale_after)
    logger.info("stale_reconcile_workflow_done", recovered=recovered)
    return recovered