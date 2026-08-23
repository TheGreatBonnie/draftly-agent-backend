"""Support resolution logic (plan §7.2).

Post-delivery bookkeeping for support threads: mark the thread resolved
when the graph delivered an answer, or leave it open for the feedback
loop when the run was interrupted/failed.
"""

from __future__ import annotations

from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)


async def resolve_support_thread(
    context: WorkflowContext,
    state: WorkflowState,
) -> dict[str, Any]:
    """Update the support thread from a finished support-graph run."""
    del context  # SupportRepository.update lands with §9 review routes
    outcome = {
        "run_id": state.run_id,
        "resolved": state.status is WorkflowStatus.DELIVERED,
        "status": state.status.value,
    }
    logger.info(
        "support_resolution run_id=%s resolved=%s",
        state.run_id,
        outcome["resolved"],
    )
    return outcome
