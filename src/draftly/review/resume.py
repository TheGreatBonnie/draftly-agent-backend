"""Unified resume for human review decisions (plan §9.6).

Loads the pending review, restores the persisted source event and its
org-scoped identity, resumes the paused workflow graph, and — only once the
workflow reached the expected terminal status — records the decision.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from draftly.review.models import ReviewDecision
from draftly.review.service import ReviewService
from draftly.workflows.state import WorkflowState

logger = structlog.get_logger(__name__)


class ReviewResumeError(Exception):
    """Raised when a review cannot be resumed or did not reach its expected status."""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _coerce_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


async def _load_event(
    events_repo: Any,
    run_id: str,
) -> dict[str, Any]:
    if events_repo is None:
        raise ReviewResumeError(f"Run {run_id} has no persisted event store")
    loader = getattr(events_repo, "get_event_by_id", None) or getattr(
        events_repo, "get_event", None
    )
    if loader is None:
        raise ReviewResumeError(f"Run {run_id} has no persisted event store")
    stored = await loader(run_id)
    if stored is None:
        raise ReviewResumeError(f"Run {run_id} has no resumable event")
    payload = _get(stored, "payload") or {}
    event = _coerce_payload(payload)
    if not event:
        raise ReviewResumeError(f"Run {run_id} has no resumable event")
    return event


async def resume_review_decision(
    *,
    review_id: str,
    approved: bool,
    reviewer_id: str,
    comment: str,
    app_state: Any,
    org_id: str | None = None,
) -> WorkflowState:
    """Resume a paused workflow after a human review decision.

    Approval requires the workflow to reach ``delivered``; rejection requires
    ``failed``. The decision is persisted only after the resume succeeds, so a
    failed resume leaves the review actionable and never records an approval
    for work that did not reach delivery.
    """
    app_state = await _resolve_app_state(app_state)
    repositories = getattr(getattr(app_state, "dependencies", None), "repositories", None)
    reviews = getattr(repositories, "reviews", None)
    if reviews is None or getattr(reviews, "get_review", None) is None:
        raise ReviewResumeError("Reviews store unavailable")

    record = await reviews.get_review(review_id)
    if record is None or str(_get(record, "status", "")) != "pending":
        raise ReviewResumeError(f"Review {review_id} is not pending")
    if org_id and str(_get(record, "org_id") or "") != org_id:
        raise ReviewResumeError(f"Review {review_id} does not belong to the organization")

    run_id = str(_get(record, "thread_id") or "")
    surface = str(_get(record, "workflow") or "")
    if surface not in ("pull_request", "issue", "support"):
        raise ReviewResumeError(f"Workflow {surface!r} is not resumable")

    tool_args = _get(record, "tool_args") or {}
    metadata = _get(record, "metadata") or {}
    interrupt_id = tool_args.get("interrupt_id") if isinstance(tool_args, dict) else None
    if not interrupt_id:
        interrupt_id = metadata.get("interrupt_id") if isinstance(metadata, dict) else None
    if not interrupt_id:
        raise ReviewResumeError("Review has no interrupt to resume")

    events_repo = getattr(repositories, "events", None)
    event = await _load_event(events_repo, run_id)
    event["project_id"] = str(_get(record, "org_id") or "")
    event["org_id"] = event["project_id"]

    runner = getattr(getattr(app_state, "workflows", None), "runner", None)
    if runner is None or getattr(runner, "resume_review", None) is None:
        raise ReviewResumeError("Workflow runner unavailable")

    from draftly.observability.metrics import metrics as _metrics

    _metrics.increment("draftly_review_decisions_total")

    try:
        state = await runner.resume_review(
            event=event,
            interrupt_id=interrupt_id,
            response={"approved": bool(approved), "comment": comment or ""},
        )
    except Exception as exc:
        logger.warning(
            "review_resume_failed",
            review_id=review_id,
            run_id=run_id,
            exc_info=True,
        )
        raise ReviewResumeError(f"Resume failed for review {review_id}") from exc

    status = getattr(getattr(state, "status", None), "value", "")
    expected = "delivered" if approved else "failed"
    if status != expected:
        raise ReviewResumeError(
            f"Review did not reach expected status {expected!r} (status={status})"
        )

    outcomes = getattr(repositories, "feedback_outcomes", None)
    service = ReviewService(repository=reviews, outcomes_repository=outcomes)
    result = await service.decide(
        ReviewDecision(
            review_id=review_id,
            reviewer_id=reviewer_id or "",
            approved=bool(approved),
            comment=comment or "",
        )
    )
    setattr(state, "decision_outcome", result)

    logger.info(
        "review_resumed",
        review_id=review_id,
        run_id=run_id,
        status=status,
        approved=bool(approved),
        reviewer_id=reviewer_id,
    )
    return state


async def _resolve_app_state(app_state: Any) -> Any:
    """Resolve the composed draftly runtime from possible wrapper shapes."""
    candidate = app_state
    # The Slack app carries the API runtime as api_app.state.draftly already;
    # keep the supplied object when it exposes the workflow runner runner.
    if candidate is None or getattr(
        getattr(candidate, "workflows", None), "runner", None
    ) is None:
        raise ReviewResumeError("Runtime not started")
    return candidate
