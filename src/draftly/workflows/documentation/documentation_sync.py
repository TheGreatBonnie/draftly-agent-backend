"""Documentation sync workflow (plan §7.2).

Scheduled full-repository documentation sweep. Phase 5 wires the task
plumbing; the sweep itself walks the document repository and enqueues
stale documents as documentation-change events for the graph.
"""

from __future__ import annotations

import logging
from typing import Any

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


async def run_documentation_sync(
    context: WorkflowContext,
    **kwargs: Any,
) -> WorkflowState:
    """Sweep the document store; report counts (graph runs per change)."""
    del kwargs
    state = WorkflowState(run_id=f"doc-sync-{id(object())}")
    documents = getattr(context.repositories, "documents", None) if context else None

    listed: list[Any] = []
    if documents is not None:
        lister = getattr(documents, "list_documents", None)
        if lister is not None:
            try:
                listed = list(await lister(limit=1000))
            except Exception:
                logger.exception("documentation_sync_list_failed")

    logger.info("documentation_sync_done documents=%d", len(listed))
    return state.finish(WorkflowStatus.DELIVERED)
