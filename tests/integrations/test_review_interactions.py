"""§9.6 verification: Slack and Discord review interactions use the shared resume."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.routes import discord as discord_routes
from draftly.persistence.repositories.reviews import ReviewRecord


def _review_record(**overrides: Any) -> ReviewRecord:
    defaults: dict[str, Any] = {
        "id": "review-1",
        "org_id": "org-1",
        "thread_id": "run-1",
        "workflow": "support",
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

    async def get_pending_by_run_id(self, run_id: str) -> ReviewRecord | None:
        if self.record and self.record.thread_id == run_id:
            return self.record
        return None

    async def get_review(self, review_id: str) -> ReviewRecord | None:
        if self.record and self.record.id == review_id:
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
    async def get_event(self, event_id: str) -> Any:
        return SimpleNamespace(
            payload={
                "event_id": event_id,
                "event_type": "slack.message",
                "source": "slack",
                "project_id": "org-1",
                "team_id": "T1",
                "channel": "C1",
                "thread_ts": "1",
            }
        )


class FakeRunner:
    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    async def resume_review(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(status=SimpleNamespace(value=self.status))


def _app_state(status: str = "delivered") -> Any:
    return SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                reviews=FakeReviews(_review_record()),
                events=FakeEvents(),
                feedback_outcomes=None,
            )
        ),
        workflows=SimpleNamespace(runner=FakeRunner(status)),
    )


async def test_slack_review_button_resumes_support_workflow(monkeypatch):
    from draftly.app.api.app import app as api_app

    app_state = _app_state()
    monkeypatch.setattr(api_app.state, "draftly", app_state, raising=False)

    from draftly.integrations.slack.app import _handle_review_action

    await _handle_review_action(
        {"value": "review-1", "user_id": "user-1", "user": {"id": "user-1"}},
        "approve_review",
    )

    runner = app_state.workflows.runner
    assert runner.calls[0]["response"] == {"approved": True, "comment": ""}
    decisions = app_state.dependencies.repositories.reviews.decisions
    assert decisions[0] == {
        "review_id": "review-1",
        "reviewer_id": "user-1",
        "decision": "approved",
        "comment": None,
    }


async def test_slack_reject_button_keeps_workflow_failed(monkeypatch):
    from draftly.app.api.app import app as api_app

    app_state = _app_state(status="failed")
    monkeypatch.setattr(api_app.state, "draftly", app_state, raising=False)

    from draftly.integrations.slack.app import _handle_review_action

    await _handle_review_action(
        {"value": "review-1", "user_id": "user-1", "user": {"id": "user-1"}},
        "reject_review",
    )

    runner = app_state.workflows.runner
    assert runner.calls[0]["response"] == {"approved": False, "comment": ""}
    decisions = app_state.dependencies.repositories.reviews.decisions
    assert decisions[0]["decision"] == "rejected"


async def test_slack_resume_failure_keeps_review_actionable(monkeypatch):
    from draftly.app.api.app import app as api_app

    app_state = _app_state(status="pending_review")
    monkeypatch.setattr(api_app.state, "draftly", app_state, raising=False)

    from draftly.integrations.slack.app import _handle_review_action

    await _handle_review_action(
        {"value": "review-1", "user_id": "user-1", "user": {"id": "user-1"}},
        "approve_review",
    )

    assert app_state.workflows.runner.calls != []
    assert app_state.dependencies.repositories.reviews.decisions == []


def test_discord_approve_button_resumes_support_workflow(monkeypatch):
    app = FastAPI()
    app.include_router(discord_routes.router, prefix="/api")
    app.state.draftly = _app_state()

    monkeypatch.setattr(discord_routes, "_verify_signature", lambda *a, **k: True)

    client = TestClient(app)
    response = client.post(
        "/api/discord/interactions",
        json={
            "type": 3,
            "data": {
                "custom_id": "discord_approve:review-1",
                "components": [{"value": "looks good"}],
            },
            "member": {"user": {"id": "user-9"}},
            "message": {"embeds": [{"description": "**Title:** Rotate keys\n\nbody"}]},
        },
        headers={"X-Signature-Ed25519": "sig", "X-Signature-Timestamp": "1"},
    )

    assert response.status_code == 200
    assert response.json()["type"] == 7
    runner = app.state.draftly.workflows.runner  # type: ignore[attr-defined]
    assert runner.calls[0]["response"] == {"approved": True, "comment": "looks good"}
    decisions = app.state.draftly.dependencies.repositories.reviews.decisions  # type: ignore[attr-defined]
    assert decisions[0]["decision"] == "approved"
