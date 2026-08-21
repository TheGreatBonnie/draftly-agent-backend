"""Documentation feedback loop workflow (plan §7.2).

Scheduled: support questions → feedback graph → documentation gaps.
Questions come from the job payload or, when absent, from recent support
messages (channel name doubles as the clustering topic).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from strands.multiagent.base import Status

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


async def run_feedback_loop(
    context: WorkflowContext,
    *,
    questions: list[dict[str, Any]] | None = None,
    gap_threshold: int = 2,
    **kwargs: Any,
) -> WorkflowState:
    """Run the deterministic feedback graph over support questions."""
    del kwargs
    state = WorkflowState(run_id=f"feedback-{id(object())}")

    if questions is None:
        questions = await _gather_questions(context)

    from draftly.orchestration.graphs.feedback_graph import build_feedback_graph

    graph = build_feedback_graph(gap_threshold=gap_threshold)
    result = await graph.invoke_async(
        json.dumps({"questions": questions}),
        invocation_state={"run_id": state.run_id},
    )
    state.result = result

    if result.status == Status.COMPLETED:
        return state.finish(WorkflowStatus.DELIVERED)
    state.errors.append(f"feedback graph ended {result.status}")
    return state.finish(WorkflowStatus.FAILED)


async def _gather_questions(context: WorkflowContext) -> list[dict[str, Any]]:
    """Collect questions via FeedbackService when wired, else raw rows."""
    feedback = getattr(context, "feedback", None) if context else None
    if feedback is not None and hasattr(feedback, "collect_questions"):
        try:
            items = await feedback.collect_questions()
            if items:
                deduplicator = getattr(feedback, "deduplicator", None)
                if deduplicator is not None:
                    items = deduplicator.deduplicate(items)
                return [
                    {
                        "topic": item.topic_key,
                        "question": item.content,
                        "source": item.platform,
                    }
                    for item in items
                ]
        except Exception:
            logger.exception("feedback_service_collect_failed")
    return await _collect_questions(context)


async def _collect_questions(context: WorkflowContext) -> list[dict[str, Any]]:
    """Fallback: fetch recent support messages as raw question dicts."""
    support = getattr(context.repositories, "support", None) if context else None
    if support is None:
        return []
    try:
        messages = await support.search_messages("%", limit=100)
    except Exception:
        logger.exception("feedback_loop_fetch_failed")
        return []
    return [
        {
            "topic": (m.channel_name or m.platform or "general").strip().lower(),
            "question": m.content,
            "source": m.platform,
        }
        for m in messages
    ]
