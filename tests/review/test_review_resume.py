"""§9.6 verification: unified review decisions resume the paused workflow.

Tests cover both approval (must reach ``delivered`` before the decision is
recorded) and rejection (must reach ``failed``), from GitHub and support
surfaces, plus org verification and event identity restore.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from draftly.persistence.repositories.reviews import ReviewRecord
from draftly.review.resume import (
    ReviewResumeError,
    resume_review_decision,
    resume_review_from_runtime,
    run_review_resume,
)


def _review_record(workflow: str = "support", **overrides: Any) -> ReviewRecord:
    defaults: dict[str, Any] = {
        "id": "review-1",
        "org_id": "org-1",
        "thread_id": "run-1",
        "workflow": workflow,
        "tool_name": "doc-review",
        "tool_args": {"interrupt_id": "int-1", "reason": {"summary": "update docs"}},
        "action_description": "support answer needs review",
        "status": "pending",
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ReviewRecord(**defaults)


class FakeReviews:
    def __init__(self, record: ReviewRecord | None) -> None:
        self.record = record
        self.decisions: list[dict[str, Any]] = []

    async def get_review(self, review_id: str) -> ReviewRecord | None:
        if self.record and self.record.id == review_id:
            return self.record
        return None

    async def get_pending_by_run_id(self, run_id: str) -> ReviewRecord | None:
        if self.record and self.record.thread_id == run_id:
            return self.record
        return None

    async def record_decision(
        self,
        *,
        review_id: str,
        reviewer_id: str,
        decision: str,
        comment: str | None = None,
    ) -> ReviewRecord:
        self.decisions.append(
            {
                "review_id": review_id,
                "reviewer_id": reviewer_id,
                "decision": decision,
                "comment": comment,
            }
        )
        assert self.record is not None
        self.record.status = decision
        return self.record


class FakeEvents:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload

    async def get_event(self, event_id: str) -> Any:
        if event_id != "run-1" or self.payload is None:
            return None
        return SimpleNamespace(payload=self.payload)


class FakeRunner:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []

    async def resume_review(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(status=SimpleNamespace(value=self.status))

    async def run(self, event: dict[str, Any]) -> Any:
        self.run_calls.append(event)
        return SimpleNamespace(status=SimpleNamespace(value=self.status))


def fake_app_state(
    *,
    workflow_status: str = "delivered",
    record: ReviewRecord | None = None,
    event_payload: dict[str, Any] | None = None,
) -> Any:
    if event_payload is None:
        event_payload = {
            "event_id": "run-1",
            "event_type": "slack.message",
            "source": "slack",
            "project_id": "org-1",
            "team_id": "T1",
            "channel": "C1",
            "thread_ts": "1",
        }
    return SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                reviews=FakeReviews(record or _review_record()),
                events=FakeEvents(event_payload),
                feedback_outcomes=None,
            )
        ),
        workflows=SimpleNamespace(runner=FakeRunner(workflow_status)),
    )


async def test_slack_approval_resumes_support_graph_and_delivers():
    app_state = fake_app_state(workflow_status="delivered")
    state = await resume_review_decision(
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        app_state=app_state,
    )
    assert state.status.value == "delivered"
    runner = app_state.workflows.runner
    assert runner.calls[0]["interrupt_id"] == "int-1"
    assert runner.calls[0]["response"] == {"approved": True, "comment": "ship it"}
    assert app_state.dependencies.repositories.reviews.decisions[0]["decision"] == "approved"


async def test_rejection_resumes_failed_workflow():
    app_state = fake_app_state(workflow_status="failed")
    state = await resume_review_decision(
        review_id="review-1",
        approved=False,
        reviewer_id="user-1",
        comment="not yet",
        app_state=app_state,
    )
    assert state.status.value == "failed"
    runner = app_state.workflows.runner
    assert runner.calls[0]["response"] == {"approved": False, "comment": "not yet"}
    assert app_state.dependencies.repositories.reviews.decisions[0]["decision"] == "rejected"


async def test_request_changes_closes_review_and_restarts_agents_with_feedback():
    app_state = fake_app_state(workflow_status="pending_review")
    state = await resume_review_decision(
        review_id="review-1",
        decision="request_changes",
        reviewer_id="user-1",
        comment="Add the migration example and explain rollback.",
        app_state=app_state,
    )

    assert state.status.value == "pending_review"
    runner = app_state.workflows.runner
    assert runner.calls == []
    assert len(runner.run_calls) == 1
    revision_event = runner.run_calls[0]
    assert revision_event["event_id"] != "run-1"
    assert revision_event["review_revision_of"] == "review-1"
    assert revision_event["review_policy"] == "always"
    assert revision_event["review_feedback"] == {
        "decision": "needs_changes",
        "comment": "Add the migration example and explain rollback.",
    }
    assert app_state.dependencies.repositories.reviews.decisions[0]["decision"] == "needs_changes"


async def test_request_changes_requires_actionable_comment():
    app_state = fake_app_state(workflow_status="pending_review")
    with pytest.raises(ReviewResumeError, match="comment"):
        await resume_review_decision(
            review_id="review-1",
            decision="request_changes",
            reviewer_id="user-1",
            comment=" ",
            app_state=app_state,
        )
    assert app_state.workflows.runner.run_calls == []
    assert app_state.dependencies.repositories.reviews.decisions == []


async def test_resume_failure_does_not_mark_approval_complete():
    reviews = FakeReviews(_review_record())
    app_state = fake_app_state(workflow_status="pending_review")
    app_state.dependencies.repositories.reviews = reviews
    with pytest.raises(ReviewResumeError):
        await resume_review_decision(
            review_id="review-1",
            approved=True,
            reviewer_id="user-1",
            comment="ship it",
            app_state=app_state,
        )
    assert reviews.decisions == []
    assert app_state.workflows.runner.calls != []  # resume was attempted


async def test_restores_support_target_metadata_for_resume():
    app_state = fake_app_state(workflow_status="delivered")
    await resume_review_decision(
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ok",
        app_state=app_state,
    )
    event = app_state.workflows.runner.calls[0]["event"]
    assert event["project_id"] == "org-1"
    assert event["team_id"] == "T1"


async def test_github_review_resumes_through_shared_service():
    app_state = fake_app_state(
        workflow_status="delivered",
        record=_review_record(workflow="pull_request"),
        event_payload={
            "event_id": "run-1",
            "event_type": "pull_request.merged",
            "repository": "acme/api",
            "source": "github",
            "actor": "dev",
            "installation_id": 55,
        },
    )
    state = await resume_review_decision(
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        app_state=app_state,
        org_id="org-1",
    )
    assert state.status.value == "delivered"
    event = app_state.workflows.runner.calls[0]["event"]
    assert event["installation_id"] == 55


async def test_org_mismatch_is_rejected_without_resume():
    app_state = fake_app_state(workflow_status="delivered")
    with pytest.raises(ReviewResumeError):
        await resume_review_decision(
            review_id="review-1",
            approved=True,
            reviewer_id="user-1",
            comment="ship it",
            app_state=app_state,
            org_id="org-2",
        )
    assert app_state.workflows.runner.calls == []


async def test_unknown_review_is_rejected():
    app_state = fake_app_state(workflow_status="delivered")
    reviews = FakeReviews(_review_record(**{"id": "other"}))
    app_state.dependencies.repositories.reviews = reviews
    with pytest.raises(ReviewResumeError):
        await resume_review_decision(
            review_id="nope",
            approved=True,
            reviewer_id="user-1",
            comment="",
            app_state=app_state,
        )
    assert app_state.workflows.runner.calls == []


async def test_runtime_core_approval_resumes_via_repositories_and_runner():
    app_state = fake_app_state(workflow_status="delivered")
    state = await resume_review_from_runtime(
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        repositories=app_state.dependencies.repositories,
        runner=app_state.workflows.runner,
    )
    assert state.status.value == "delivered"
    runner = app_state.workflows.runner
    assert runner.calls[0]["interrupt_id"] == "int-1"
    assert runner.calls[0]["response"] == {"approved": True, "comment": "ship it"}
    assert app_state.dependencies.repositories.reviews.decisions[0]["decision"] == "approved"


async def test_runtime_core_requires_runner():
    app_state = fake_app_state(workflow_status="delivered")
    with pytest.raises(ReviewResumeError, match="Workflow runner unavailable"):
        await resume_review_from_runtime(
            review_id="review-1",
            approved=True,
            reviewer_id="user-1",
            comment="x",
            repositories=app_state.dependencies.repositories,
            runner=None,
        )


async def test_adapter_delegates_to_runtime_core():
    app_state = fake_app_state(workflow_status="delivered")
    reviews = app_state.dependencies.repositories.reviews
    core_state = await resume_review_from_runtime(
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        repositories=app_state.dependencies.repositories,
        runner=app_state.workflows.runner,
    )
    # record_decision sets the record to non-pending; restore status so the
    # adapter call exercises the same pending-review path.
    reviews.record.status = "pending"
    adapter_state = await resume_review_decision(
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        app_state=app_state,
    )
    assert core_state.status.value == "delivered"
    assert adapter_state.status.value == "delivered"
    assert len(reviews.decisions) == 2


async def test_approval_paused_by_steering_intervention_keeps_review_actionable():
    """A pending_intervention outcome pauses delivery: do NOT record the
    decision or raise — the durable steering intervention drives the resume."""
    reviews = FakeReviews(_review_record())
    app_state = fake_app_state(
        workflow_status="pending_intervention",
        record=_review_record(workflow="pull_request"),
        event_payload={
            "event_id": "run-1",
            "event_type": "pull_request.merged",
            "repository": "acme/api",
            "source": "github",
            "actor": "dev",
            "installation_id": 55,
        },
    )
    app_state.dependencies.repositories.reviews = reviews
    state = await resume_review_decision(
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        app_state=app_state,
        org_id="org-1",
    )
    assert state.status.value == "pending_intervention"
    assert reviews.decisions == []  # treated as not-yet-delivered, not approved
    assert app_state.workflows.runner.calls[0]["interrupt_id"] == "int-1"


async def test_worker_workflow_resumes_approval_against_composed_context():
    app_state = fake_app_state(workflow_status="delivered")
    context = SimpleNamespace(
        repositories=app_state.dependencies.repositories,
        runner=app_state.workflows.runner,
    )
    state = await run_review_resume(
        context,
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        org_id="org-1",
    )
    assert state.status.value == "delivered"
    runner = app_state.workflows.runner
    assert runner.calls[0]["response"] == {"approved": True, "comment": "ship it"}
    assert app_state.dependencies.repositories.reviews.decisions[0]["decision"] == "approved"


async def test_worker_workflow_noops_when_review_already_decided():
    reviews = FakeReviews(_review_record(status="approved"))
    runner = FakeRunner("delivered")
    context = SimpleNamespace(
        repositories=SimpleNamespace(
            reviews=reviews,
            events=FakeEvents(
                {
                    "event_id": "run-1",
                    "event_type": "pull_request.merged",
                    "repository": "acme/api",
                    "source": "github",
                }
            ),
            feedback_outcomes=None,
        ),
        runner=runner,
    )
    result = await run_review_resume(
        context,
        review_id="review-1",
        approved=True,
        reviewer_id="user-1",
        comment="ship it",
        org_id="org-1",
    )
    assert result is None
    assert runner.calls == []
    assert reviews.decisions == []
