"""§9.4 route smoke tests — mocked stores, no real runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes import documentation, evaluations, github, support
from draftly.observability.audit import AuditTrail


def _review_record(**overrides: Any) -> Any:
    from draftly.persistence.repositories.reviews import ReviewRecord

    defaults: dict[str, Any] = {
        "id": "rev-1",
        "org_id": "org-1",
        "thread_id": "run-1",
        "workflow": "pull_request",
        "tool_name": "doc-review",
        "tool_args": {"interrupt_id": "int-1"},
        "action_description": "merge docs",
        "status": "pending",
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ReviewRecord(**defaults)


class FakeReviewsRepository:
    def __init__(self, record: Any = None) -> None:
        self.record = record
        self.decisions: list[dict[str, Any]] = []

    async def get_pending_by_run_id(self, run_id: str) -> Any:
        if self.record and self.record.thread_id == run_id:
            return self.record
        return None

    async def get_review(self, review_id: str) -> Any:
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
    ) -> Any:
        self.decisions.append(
            {
                "review_id": review_id,
                "reviewer_id": reviewer_id,
                "decision": decision,
                "comment": comment,
            }
        )
        self.record.status = decision
        return self.record


class FakeEventsRepository:
    async def get_event(self, event_id: str) -> Any:
        if event_id != "run-1":
            return None
        return SimpleNamespace(
            payload={
                "event_id": event_id,
                "event_type": "pull_request.merged",
                "repository": "acme/api",
                "source": "github",
                "actor": "dev",
            }
        )


class FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []

    async def resume_review(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        status = "delivered" if kwargs["response"]["approved"] else "failed"
        return SimpleNamespace(status=SimpleNamespace(value=status))

    async def run(self, event: dict[str, Any]) -> Any:
        self.run_calls.append(event)
        return SimpleNamespace(
            run_id=event["event_id"],
            status=SimpleNamespace(value="pending_review"),
        )


class FakeDocumentsRepository:
    async def list_by_org(
        self, *, org_id: str, limit: int = 1000
    ) -> list[dict]:
        return [
            {
                "id": "doc-1",
                "org_id": org_id,
                "repository": "acme/api",
                "status": "indexed",
            }
        ]

    async def find_by_repository(self, *, repository: str) -> list[dict]:
        return [{"id": "doc-1", "repository": repository}]

    async def get(self, *, document_id: str) -> dict | None:
        if document_id == "doc-1":
            return {
                "id": "doc-1",
                "org_id": "org-1",
                "content": "# hi",
            }
        return None


class FakeEvaluationsRepository:
    async def search(
        self,
        *,
        org_id: str,
        evaluation_type: str | None,
        limit: int,
    ) -> list[dict]:
        return [{"id": "eval-1", "score": 0.9, "status": "passed"}]


class FakeSupportRepository:
    async def search_messages(self, query: str, *, platform=None, limit=20):
        return []

    async def get_thread(self, thread_id: str):
        from draftly.support.models import SupportMessage, SupportThread

        if thread_id != "t-9":
            return None
        message = SupportMessage(
            id="m-1",
            platform="slack",
            channel_id="c-1",
            thread_id=thread_id,
            author_name="amy",
            content="how do I rotate keys?",
        )
        return SupportThread(
            id=thread_id,
            platform="slack",
            messages=[message],
        )


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(github.router, prefix="/api")
    app.include_router(documentation.router, prefix="/api")
    app.include_router(evaluations.router, prefix="/api")
    app.include_router(support.router, prefix="/api")
    app.dependency_overrides[get_verified_token] = lambda: {
        "user_id": "tester",
        "org_id": "org-1",
        "org_role": "reviewer",
    }

    reviews = FakeReviewsRepository(_review_record())
    state = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                reviews=reviews,
                events=FakeEventsRepository(),
                documents=FakeDocumentsRepository(),
                evaluations=FakeEvaluationsRepository(),
                support=FakeSupportRepository(),
            )
        ),
        workflows=SimpleNamespace(runner=FakeWorkflowRunner()),
        worker=None,
    )
    app.state.draftly = state
    return TestClient(app)


class TestDocumentationRoutes:
    def test_list_documents(self, client: TestClient) -> None:
        response = client.get("/api/documentation", params={"repository": "acme/api"})
        assert response.status_code == 200
        assert response.json()["items"][0]["id"] == "doc-1"

    def test_get_document_detail(self, client: TestClient) -> None:
        response = client.get("/api/documentation/doc-1")
        assert response.status_code == 200
        assert response.json()["content"] == "# hi"

    def test_get_missing_document_404(self, client: TestClient) -> None:
        assert client.get("/api/documentation/nope").status_code == 404


class TestEvaluationRoutes:
    def test_list_evaluations(self, client: TestClient) -> None:
        response = client.get("/api/evaluations")
        assert response.status_code == 200
        assert response.json()["items"][0]["score"] == 0.9

    def test_run_without_runtime_503(self, client: TestClient) -> None:
        assert client.post("/api/evaluations/run").status_code == 503


class TestSupportRoutes:
    def test_list_questions_empty(self, client: TestClient) -> None:
        response = client.get("/api/support/questions")
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_get_question_detail(self, client: TestClient) -> None:
        response = client.get("/api/support/questions/t-9")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "t-9"
        assert body["messages"][0]["content"].startswith("how do I rotate")

    def test_get_missing_thread_404(self, client: TestClient) -> None:
        assert client.get("/api/support/questions/none").status_code == 404


class TestReviewResumeRoute:
    def test_resume_unknown_run_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/github/review/missing-run",
            json={
                "review_id": "rev-1",
                "reviewer_id": "u-1",
                "approved": True,
            },
        )
        assert response.status_code == 404

    def test_reject_records_decision_no_resume(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        response = client.post(
            "/api/github/review/run-1",
            json={
                "review_id": "rev-1",
                "reviewer_id": "u-1",
                "approved": False,
                "comment": "wrong approach",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        state = client.app.state.draftly  # type: ignore[attr-defined]
        assert state.dependencies.repositories.reviews.decisions[0]["decision"] == "rejected"
        assert state.workflows.runner.calls[0]["response"]["approved"] is False

    def test_approve_resumes_graph(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        response = client.post(
            "/api/github/review/run-1",
            json={
                "review_id": "rev-1",
                "reviewer_id": "u-1",
                "approved": True,
                "comment": "ship it",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resumed"
        assert body["workflow_status"] == "delivered"
        runner_call = client.app.state.draftly.workflows.runner.calls[0]  # type: ignore[attr-defined]
        assert runner_call["interrupt_id"] == "int-1"
        assert runner_call["response"]["approved"] is True
        assert runner_call["event"]["project_id"] == "org-1"

    def test_request_changes_restarts_agents_with_feedback(self, client: TestClient) -> None:
        response = client.post(
            "/api/github/review/run-1",
            json={
                "review_id": "rev-1",
                "reviewer_id": "u-1",
                "decision": "request_changes",
                "comment": "Add the migration example.",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "needs_changes"
        assert body["workflow_status"] == "pending_review"
        runner = client.app.state.draftly.workflows.runner  # type: ignore[attr-defined]
        assert len(runner.run_calls) == 1
        revision_event = runner.run_calls[0]
        assert revision_event["review_feedback"]["decision"] == "needs_changes"
        assert revision_event["review_feedback"]["comment"] == "Add the migration example."
        decisions = client.app.state.draftly.dependencies.repositories.reviews.decisions  # type: ignore[attr-defined]
        assert decisions[0]["decision"] == "needs_changes"

    def test_approve_with_dashboard_body_without_review_id(
        self, client: TestClient
    ) -> None:
        """The dashboard sends decision/reviewer_id/comment and no review_id.

        Regression: ReviewDecision treated review_id as required, so the UI's
        payload (which relies on the server-stored review identity) 422'd.
        """
        response = client.post(
            "/api/github/review/run-1",
            json={
                "decision": "approve",
                "reviewer_id": "",
                "comment": "ship it",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resumed"
        assert body["workflow_status"] == "delivered"

    def test_comment_null_accepted(self, client: TestClient) -> None:
        """A dashboard without a comment sends comment: null, which is valid."""
        response = client.post(
            "/api/github/review/run-1",
            json={
                "decision": "reject",
                "reviewer_id": "u-1",
                "comment": None,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

    def test_approve_non_resumable_surface_409(self, client: TestClient) -> None:
        state = client.app.state.draftly  # type: ignore[attr-defined]
        repo = state.dependencies.repositories.reviews
        repo.record.workflow = "unknown_surface"
        response = client.post(
            "/api/github/review/run-1",
            json={"review_id": "rev-1", "reviewer_id": "u-1", "approved": True},
        )
        assert response.status_code == 409


class TestAuditTrailRows:
    async def test_run_outcome_recorded_with_sink(self) -> None:
        rows: list[dict[str, Any]] = []

        async def sink(entry: dict[str, Any]) -> None:
            rows.append(entry)

        trail = AuditTrail(sink=sink)
        await trail.record_run(
            run_id="run-fake",
            workflow="github_pr",
            status="COMPLETED",
        )
        assert len(rows) == 1
        assert rows[0]["action"] == "workflow_run"
        assert rows[0]["target"] == "run-fake"
        assert rows[0]["outcome"] == "COMPLETED"
        assert trail.recent()[0]["target"] == "run-fake"

    async def test_sink_failure_never_raises(self) -> None:
        async def bad_sink(entry: dict[str, Any]) -> None:
            raise RuntimeError("db down")

        trail = AuditTrail(sink=bad_sink)
        entry = await trail.record(actor="u", action="x")
        assert entry["action"] == "x"
