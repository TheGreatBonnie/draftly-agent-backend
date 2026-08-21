"""GitHub release workflow (plan §7.2).

A published release triggers the documentation graph against the release
diff surface (same graph, release event type).
"""

from __future__ import annotations

import logging
from typing import Any

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState

logger = logging.getLogger(__name__)


async def run_release_workflow(
    context: WorkflowContext,
    event: dict[str, Any],
) -> WorkflowState:
    """Run the documentation graph for one release event."""
    runner = WorkflowRunner(context)
    state = await runner.run(event)
    logger.info(
        "release_workflow_done run_id=%s status=%s",
        state.run_id,
        state.status.value,
    )
    return state
