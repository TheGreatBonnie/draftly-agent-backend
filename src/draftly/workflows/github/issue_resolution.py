"""GitHub issue resolution workflow (plan §7.2)."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState

logger = structlog.get_logger(__name__)


async def run_github_issue_workflow(
    context: WorkflowContext,
    event: dict[str, Any],
) -> WorkflowState:
    """Run the issue graph for one GitHub issue event."""
    runner = WorkflowRunner(context)
    state = await runner.run(event)
    logger.info(
        "issue_workflow_done run_id=%s status=%s",
        state.run_id,
        state.status.value,
    )
    return state
