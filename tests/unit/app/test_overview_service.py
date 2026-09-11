"""Overview snapshot aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from draftly.app.services import overview
from draftly.app.services.overview import build_overview_snapshot
from draftly.persistence.repositories.reviews import ReviewRecord


class FakeDocuments:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def list_by_org(self, *, org_id: str, limit: int) -> list[dict[str, Any]]:
        return [item for item in self._items if item["org_id"] == org_id][:limit]


class FakeReviews:
    def __init__(self, items: list[ReviewRecord]) -> None:
        self._items = items

    async def list_reviews(
        self, *, status: str | None, org_id: str, limit: int
    ) -> list[ReviewRecord]:
        return [
            item
            for item in self._items
            if item.org_id == org_id and (status is None or item.status == status)
        ][:limit]


class FakeEvaluations:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def search(
        self, *, org_id: str, evaluation_type: None, limit: int
    ) -> list[dict[str, Any]]:
        return [item for item in self._items if item["org_id"] == org_id][:limit]


class FakeJobs:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.org_ids: list[str | None] = []

    async def list_active(self, org_id: str | None = None) -> list[dict[str, Any]]:
        self.org_ids.append(org_id)
        return [item for item in self.items if item["org_id"] == org_id]


class FakeSteeringInterventions:
    def __init__(self, count: int) -> None:
        self.count = count
        self.org_ids: list[str] = []

    async def count_pending_for_org(self, *, org_id: str) -> int:
        self.org_ids.append(org_id)
        return self.count


class FakeGitHubInstallations:
    async def list_by_org(self, org_id: str) -> list[dict[str, str]]:
        return [{"id": "github-installation"}] if org_id == "org-a" else []


class IntegrationDatabase:
    async def fetch_all(self, query: str, *args: object) -> list[dict[str, str]]:
        assert args == ("org-a",)
        if "slack_installations" in query:
            raise RuntimeError("Slack store unavailable")
        return []

    async def fetch_one(self, query: str, *args: object) -> dict[str, str] | None:
        assert args == ("org-a",)
        if "organizations" in query:
            return {"discord_guild_id": "guild-1"}
        return None


def _empty_application() -> SimpleNamespace:
    return SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                documents=FakeDocuments([]),
                reviews=FakeReviews([]),
                evaluations=FakeEvaluations([]),
            ),
            integrations=SimpleNamespace(database=object()),
        )
    )


def test_evaluation_summary_exposes_only_dashboard_quality_dimensions() -> None:
    evaluations = [
        {
            "score": 0.8,
            "created_at": datetime(2026, 9, 9, tzinfo=UTC),
            "metrics": {
                "granular": [
                    {"metric": "completeness", "score": 0.8},
                    {"metric": "correctness", "score": 0.9},
                    {"metric": "groundedness", "score": 0.7},
                    {"metric": "relevance", "score": 0.85},
                    {"metric": "expected_tools", "score": 1.0},
                    {"metric": "node:content_blog", "score": 0.95},
                ]
            },
        }
    ]

    summary, _, _ = overview._evaluation_summary(evaluations)

    assert summary["dimensions"] == [
        {"name": "completeness", "value": 80.0},
        {"name": "correctness", "value": 90.0},
        {"name": "groundedness", "value": 70.0},
        {"name": "relevance", "value": 85.0},
    ]


@pytest.mark.asyncio
async def test_overview_aggregates_org_scoped_document_review_and_evaluation_signals() -> None:
    """Removing any source aggregation must break the corresponding snapshot data."""
    now = datetime.now(UTC).replace(microsecond=0)
    yesterday = now - timedelta(days=1)
    pending = ReviewRecord(
        id="review-pending",
        org_id="org-a",
        thread_id="run-1",
        workflow="documentation",
        tool_name="doc-review",
        tool_args={},
        action_description="Review the deployment guide",
        status="pending",
        detail={"risk": "high", "evaluation": {"score": 0.79}},
        created_at=now,
    )
    decided = ReviewRecord(
        id="review-decided",
        org_id="org-a",
        thread_id="run-2",
        workflow="documentation",
        tool_name="doc-review",
        tool_args={},
        action_description="",
        status="approved",
        decision="approved",
        decided_at=now,
        created_at=yesterday,
    )
    application = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                documents=FakeDocuments(
                    [
                        {
                            "id": "doc-a",
                            "org_id": "org-a",
                            "title": "Deployment guide",
                            "repository": "acme/docs",
                            "path": "docs/deploy.md",
                            "status": "indexed",
                            "stale": True,
                            "created_at": yesterday,
                            "updated_at": now,
                        },
                        {
                            "id": "doc-b",
                            "org_id": "org-b",
                            "title": "Foreign document",
                            "repository": "other/docs",
                            "path": "docs/secret.md",
                            "status": "published",
                            "stale": False,
                            "created_at": now,
                            "updated_at": now,
                        },
                    ]
                ),
                reviews=FakeReviews([pending, decided]),
                evaluations=FakeEvaluations(
                    [
                        {
                            "id": "evaluation-latest",
                            "org_id": "org-a",
                            "score": 0.9,
                            "status": "running",
                            "created_at": now,
                            "metrics": {"granular": [{"metric": "Correctness", "score": 0.9}]},
                        },
                        {
                            "id": "evaluation-earlier",
                            "org_id": "org-a",
                            "score": 80,
                            "status": "completed",
                            "created_at": yesterday,
                            "metrics": {"granular": [{"metric": "Correctness", "score": 80}]},
                        },
                        {
                            "id": "evaluation-foreign",
                            "org_id": "org-b",
                            "score": 0,
                            "status": "failed",
                            "created_at": now,
                            "metrics": {},
                        },
                    ]
                ),
            )
        )
    )

    snapshot = await build_overview_snapshot(application, "org-a", 7)

    assert snapshot["summary"] == {
        "documentation_total": 1,
        "active_workflows": 0,
        "running_workflows": 0,
        "scheduled_workflows": 0,
        "pending_reviews": 1,
        "average_evaluation_score": 85.0,
    }
    assert snapshot["attention"] == {
        "pending_reviews": 1,
        "pending_interventions": 0,
        "high_risk_reviews": 1,
        "failed_evaluations": 0,
        "integration_issues": 3,
        "stale_documentation": 1,
    }
    assert snapshot["system"]["evaluations_status"] == "Running"
    assert snapshot["recent_changes"] == [
        {
            "id": "doc-a",
            "title": "Deployment guide",
            "detail": "acme/docs · docs/deploy.md",
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "status": "published",
            "href": "/documentation/doc-a",
        }
    ]
    assert snapshot["evaluation"] == {
        "average_score": 85.0,
        "trend": 10.0,
        "dimensions": [{"name": "correctness", "value": 85.0}],
    }
    by_date = {point["date"]: point for point in snapshot["activity"]}
    assert by_date[yesterday.date().isoformat()]["created"] == 1
    assert by_date[now.date().isoformat()] == {
        "date": now.date().isoformat(),
        "created": 0,
        "updated": 1,
        "reviewed": 1,
        "published": 1,
    }


@pytest.mark.asyncio
async def test_overview_returns_only_newest_active_workflows_with_normalized_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning terminal or unnormalized workflows would misstate active work."""
    latest = datetime(2026, 9, 9, 12, tzinfo=UTC)
    earlier = latest - timedelta(hours=1)

    async def workflows_for_org(
        *, org_id: str, db: object
    ) -> list[dict[str, Any]]:
        assert org_id == "org-a"
        assert db is not None
        return [
            {
                "run_id": "workflow-running",
                "title": "Update deployment guide",
                "repository": "acme/docs",
                "status": "RUNNING",
                "created_at": latest,
            },
            {
                "run_id": "workflow-queued",
                "title": "Review onboarding guide",
                "repository": "acme/docs",
                "status": "queued",
                "created_at": earlier,
            },
            {
                "run_id": "workflow-completed",
                "title": "Completed workflow",
                "repository": "acme/docs",
                "status": "completed",
                "created_at": latest,
            },
        ]

    monkeypatch.setattr(
        overview, "list_github_workflows_record", workflows_for_org, raising=False
    )

    snapshot = await build_overview_snapshot(_empty_application(), "org-a", 14)

    assert snapshot["summary"]["active_workflows"] == 2
    assert snapshot["summary"]["running_workflows"] == 1
    assert snapshot["summary"]["scheduled_workflows"] == 1
    assert snapshot["active_workflows"] == [
        {
            "id": "workflow-running",
            "name": "Update deployment guide",
            "repository": "acme/docs",
            "status": "running",
            "timestamp": "2026-09-09T12:00:00Z",
            "href": "/workflows/workflow-running",
        },
        {
            "id": "workflow-queued",
            "name": "Review onboarding guide",
            "repository": "acme/docs",
            "status": "queued",
            "timestamp": "2026-09-09T11:00:00Z",
            "href": "/workflows/workflow-queued",
        },
    ]


@pytest.mark.asyncio
async def test_overview_includes_org_scoped_pending_intervention_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _empty_application()
    steering = FakeSteeringInterventions(2)
    application.dependencies.repositories.steering_interventions = steering
    async def no_workflows(**_: object) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(overview, "list_github_workflows_record", no_workflows)

    snapshot = await build_overview_snapshot(application, "org-a", 14)

    assert snapshot["attention"]["pending_interventions"] == 2
    assert steering.org_ids == ["org-a"]


@pytest.mark.asyncio
async def test_overview_isolates_a_failed_integration_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One unavailable integration must not turn connected sources into failures."""
    application = _empty_application()
    application.dependencies.repositories.github_installations = FakeGitHubInstallations()
    application.dependencies.integrations.database = IntegrationDatabase()
    async def no_workflows(**_: object) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(
        overview,
        "list_github_workflows_record",
        no_workflows,
    )

    snapshot = await build_overview_snapshot(application, "org-a", 14)

    assert snapshot["system"]["data_sources_total"] == 3
    assert snapshot["system"]["data_sources_connected"] == 2
    assert snapshot["attention"]["integration_issues"] == 1


@pytest.mark.asyncio
async def test_overview_derives_scheduler_status_from_org_scoped_active_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _empty_application()
    jobs = FakeJobs([{"org_id": "org-a", "id": "job-1"}])
    application.dependencies.repositories.jobs = jobs

    async def no_workflows(**_: object) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(overview, "list_github_workflows_record", no_workflows)

    snapshot = await build_overview_snapshot(application, "org-a", 14)

    assert snapshot["system"]["scheduler_status"] == "Healthy"
    assert jobs.org_ids == ["org-a"]
