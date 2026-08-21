"""Issue feedback processing (plan §7.2).

Issue threads are a second support signal: resolved issues feed the
feedback loop as candidate documentation gaps. Phase 5 records the
normalized signal; clustering happens in the scheduled feedback graph.
"""

from __future__ import annotations

import logging
from typing import Any

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


async def process_issue_feedback(
    context: WorkflowContext,
    event: dict[str, Any],
) -> WorkflowState:
    """Record an issue-derived feedback signal."""
    del context  # persistence wiring lands with the feedback store
    state = WorkflowState(run_id=str(event.get("event_id") or "issue-feedback"))
    state.event = event

    issue = event.get("issue") or {}
    state.result = {
        "topic": (issue.get("title") or "").strip().lower() or "general",
        "question": issue.get("title", ""),
        "source": "github_issue",
        "state": issue.get("state", ""),
    }
    logger.info("issue_feedback_recorded run_id=%s", state.run_id)
    return state.finish(WorkflowStatus.DELIVERED)
