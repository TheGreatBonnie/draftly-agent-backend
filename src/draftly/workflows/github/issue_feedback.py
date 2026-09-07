"""Issue feedback processing (plan §7.2).

Issue threads are a second support signal: resolved issues feed the
feedback loop as candidate documentation gaps. Phase 5 records the
normalized signal; clustering happens in the scheduled feedback graph.
"""

from __future__ import annotations

from typing import Any

from draftly.workflows.context import WorkflowContext
from draftly.workflows.github.feedback_ingestion import ingest_github_feedback
from draftly.workflows.state import WorkflowState


async def process_issue_feedback(
    context: WorkflowContext,
    event: dict[str, Any],
) -> WorkflowState:
    """Record an issue-derived feedback signal."""
    return await ingest_github_feedback(context, event)
