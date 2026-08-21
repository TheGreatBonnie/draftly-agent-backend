"""Slack support workflow (plan §7.2)."""

from __future__ import annotations

import logging
from typing import Any

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState

logger = logging.getLogger(__name__)


async def run_slack_support(
    context: WorkflowContext,
    event: dict[str, Any],
) -> WorkflowState:
    """Run the support graph for one Slack question."""
    runner = WorkflowRunner(context)
    state = await runner.run(event)
    logger.info(
        "slack_support_done run_id=%s status=%s",
        state.run_id,
        state.status.value,
    )
    return state
