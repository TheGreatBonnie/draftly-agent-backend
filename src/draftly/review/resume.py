"""Unified resume for human review decisions (plan §9.6).

Loads the pending review, restores the persisted source event and its
org-scoped identity, resumes the paused workflow graph, and — only once the
workflow reached the expected terminal status — records the decision.
"""

from __future__ import annotations

import json
from typing import Any, Literal, cast
from uuid import uuid4

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


async def resume_review_from_runtime(
    *,
    review_id: str,
    approved: bool | None = None,
    decision: Literal["approve", "request_changes", "reject"] | None = None,
    reviewer_id: str,
    comment: str,
    repositories: Any,
    runner: Any,
    org_id: str | None = None,
) -> WorkflowState:
    """Resume or revise a paused workflow after a human review decision.

    Runtime-agnostic core: takes the concrete repositories and workflow
    runner instead of a composed app object so the same code path serves the
    inline API routes and the durable worker (see ``run_review_resume``).
    """
    if decision is None:
        if approved is None:
            raise ReviewResumeError("A review decision is required")
        decision = "approve" if approved else "reject"
    approved_value = decision == "approve"
    if decision == "request_changes" and not comment.strip():
        raise ReviewResumeError("Request changes requires a comment")

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

    if decision == "request_changes":
        outcomes = getattr(repositories, "feedback_outcomes", None)
        service = ReviewService(repository=reviews, outcomes_repository=outcomes)
        result = await service.decide(
            ReviewDecision(
                review_id=review_id,
                reviewer_id=reviewer_id or "",
                approved=False,
                decision="request_changes",
                comment=comment.strip(),
            )
        )

        revision_event = dict(event)
        revision_event["event_id"] = str(uuid4())
        revision_event["review_policy"] = "always"
        revision_event["review_revision_of"] = review_id
        revision_event["review_feedback"] = {
            "decision": "needs_changes",
            "comment": comment.strip(),
        }
        marker = getattr(events_repo, "mark_status", None)
        if marker is not None:
            try:
                await marker(run_id, "needs_changes")
            except Exception:
                logger.warning(
                    "review_revision_original_event_mark_failed",
                    run_id=run_id,
                    exc_info=True,
                )
        if runner is None or getattr(runner, "run", None) is None:
            raise ReviewResumeError("Workflow runner unavailable")
        revised_state = await runner.run(revision_event)
        setattr(revised_state, "decision_outcome", result)
        setattr(revised_state, "review_revision_of", review_id)
        return cast(WorkflowState, revised_state)

    if runner is None or getattr(runner, "resume_review", None) is None:
        raise ReviewResumeError("Workflow runner unavailable")

    from draftly.observability.metrics import metrics as _metrics

    _metrics.increment("draftly_review_decisions_total")

    try:
        state = await runner.resume_review(
            event=event,
            interrupt_id=interrupt_id,
            response={"approved": approved_value, "comment": comment or ""},
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
    expected = "delivered" if approved_value else "failed"
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
            approved=approved_value,
            decision=decision,
            comment=comment or "",
        )
    )
    setattr(state, "decision_outcome", result)

    logger.info(
        "review_resumed",
        review_id=review_id,
        run_id=run_id,
        status=status,
        approved=approved_value,
        reviewer_id=reviewer_id,
    )
    return cast(WorkflowState, state)


async def resume_review_decision(
    *,
    review_id: str,
    approved: bool | None = None,
    decision: Literal["approve", "request_changes", "reject"] | None = None,
    reviewer_id: str,
    comment: str,
    app_state: Any,
    org_id: str | None = None,
) -> WorkflowState:
    """Resume or revise a paused workflow after an inline API review decision.

    Thin adapter over ``resume_review_from_runtime`` for the platform routes
    that still hold the composed runtime (GitHub, Slack, Discord, service).
    """
    app_state = await _resolve_app_state(app_state)
    repositories = getattr(getattr(app_state, "dependencies", None), "repositories", None)
    runner = getattr(getattr(app_state, "workflows", None), "runner", None)
    return await resume_review_from_runtime(
        review_id=review_id,
        approved=approved,
        decision=decision,
        reviewer_id=reviewer_id,
        comment=comment,
        repositories=repositories,
        runner=runner,
        org_id=org_id,
    )


async def run_review_resume(
    context: Any,
    **kwargs: Any,
) -> WorkflowState | None:
    """Worker/registry workflow: resume a review against the composed context.

    Bound to the composed ``WorkflowContext`` by ``build_task_runner`` so the
    RQ worker and the in-process fallback run the same path as the API routes.
    A review that is no longer pending was already decided by a prior (possibly
    retried) attempt — treat it as a success so RQ's Retry policy cannot
    double-resume a finished review.
    """
    repositories = getattr(context, "repositories", None)
    runner = getattr(context, "runner", None)
    if repositories is None or runner is None:
        raise ReviewResumeError(
            "Workflow context is not composed (missing repositories/runner)"
        )
    try:
        return await resume_review_from_runtime(
            repositories=repositories,
            runner=runner,
            **kwargs,
        )
    except ReviewResumeError as exc:
        if "is not pending" in str(exc):
            logger.info(
                "review_resume_already_handled",
                review_id=kwargs.get("review_id", ""),
            )
            return None
        raise


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
