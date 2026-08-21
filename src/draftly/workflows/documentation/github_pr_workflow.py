"""GitHub pull-request workflow (plan §7.2).

Thin adapter: normalized PR event → WorkflowRunner (documentation graph).
The runner owns idempotency, graph build, invocation, and outcomes.
"""

from __future__ import annotations

import logging
from typing import Any

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState

logger = logging.getLogger(__name__)


async def run_pull_request_workflow(
    context: WorkflowContext,
    event: dict[str, Any],
) -> WorkflowState:
    """Run the documentation graph for one pull-request event."""
    runner = WorkflowRunner(context)
    state = await runner.run(event)
    logger.info(
        "pr_workflow_done run_id=%s status=%s",
        state.run_id,
        state.status.value,
    )
    return state
