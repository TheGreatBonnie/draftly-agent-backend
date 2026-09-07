"""Persist normalized GitHub feedback signals."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.feedback.models import FeedbackItem
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)


def _feedback_item(event: dict[str, Any]) -> FeedbackItem:
    payload = event.get("feedback") or {}
    issue = event.get("issue") or {}
    pull_request = event.get("pull_request") or {}
    content = str(payload.get("content") or issue.get("body") or issue.get("title") or "").strip()
    source_event_id = str(
        payload.get("source_event_id")
        or event.get("event_id")
        or payload.get("source_message_id")
        or ""
    )
    if not content:
        raise ValueError("GitHub feedback event has no content")
    if not source_event_id:
        raise ValueError("GitHub feedback event has no source event id")

    source_url = payload.get("source_url") or issue.get("html_url") or pull_request.get("html_url")
    return FeedbackItem(
        id=source_event_id,
        org_id=str(event.get("project_id") or event.get("org_id") or "") or None,
        platform=str(payload.get("platform") or "github"),
        topic=payload.get("topic") or issue.get("title") or pull_request.get("title"),
        content=content,
        author=event.get("actor"),
        channel=event.get("repository"),
        source_message_id=payload.get("source_message_id") or source_event_id,
        source_event_id=source_event_id,
        source_url=source_url,
        category=str(payload.get("category") or "question"),
        sentiment=str(payload.get("sentiment") or "neutral"),
    )


async def ingest_github_feedback(
    context: WorkflowContext,
    event: dict[str, Any],
) -> WorkflowState:
    """Persist one normalized GitHub feedback event."""
    run_id = str(event.get("event_id") or "github-feedback")
    state = WorkflowState(run_id=run_id, event=event)
    repository = getattr(getattr(context, "repositories", None), "feedback", None)
    if repository is None:
        state.errors.append("feedback repository is unavailable")
        return state.finish(WorkflowStatus.FAILED)
    try:
        item = _feedback_item(event)
        await repository.save_signal(item)
    except Exception as exc:
        logger.exception("github_feedback_persist_failed", run_id=run_id)
        state.errors.append(str(exc))
        return state.finish(WorkflowStatus.FAILED)

    state.result = {
        "source_event_id": item.source_event_id,
        "platform": item.platform,
        "topic": item.topic,
    }
    logger.info("github_feedback_persisted", run_id=run_id, source_event_id=item.source_event_id)
    return state.finish(WorkflowStatus.DELIVERED)
