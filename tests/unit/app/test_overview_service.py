"""Overview snapshot aggregation."""

from __future__ import annotations

import asyncio
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


class FakePageQuality:
    """Stands in for PageQualityRepository at the overview boundary."""

    def __init__(self, summary: dict[str, Any] | None = None) -> None:
        self._summary = summary or {
            "average_score": None,
            "total_runs": 0,
            "scored_pages": 0,
            "total_pages": 0,
            "passed": 0,
            "needs_revision": 0,
            "awaiting_human": 0,
            "by_metric": [],
            "trend": [],
        }
        self.calls: list[tuple[str, int]] = []

    async def summary(self, org_id: str, days: int) -> dict[str, Any]:
        self.calls.append((org_id, days))
        return self._summary


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


class FakeAgentRuns:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def list_agent_summaries(self, *, org_id: str, limit: int) -> list[dict[str, Any]]:
        self.calls.append((org_id, limit))
        return [
            {"id": "classifier", "last_run_status": "completed"},
            {"id": "writer", "last_run_status": "failed"},
            {"id": "researcher", "last_run_status": "idle"},
        ]


class ReadBarrier:
    def __init__(self, expected: int) -> None:
        self.expected = expected
        self.started = 0
        self.ready = asyncio.Event()

    async def arrive(self) -> None:
        self.started += 1
        if self.started == self.expected:
            self.ready.set()
        await self.ready.wait()


class ConcurrentDocuments(FakeDocuments):
    def __init__(self, barrier: ReadBarrier) -> None:
        super().__init__([])
        self.barrier = barrier

    async def list_by_org(self, *, org_id: str, limit: int) -> list[dict[str, Any]]:
        await self.barrier.arrive()
        return await super().list_by_org(org_id=org_id, limit=limit)


class ConcurrentReviews(FakeReviews):
    def __init__(self, barrier: ReadBarrier) -> None:
        super().__init__([])
        self.barrier = barrier

    async def list_reviews(
        self, *, status: str | None, org_id: str, limit: int
    ) -> list[ReviewRecord]:
        await self.barrier.arrive()
        return await super().list_reviews(status=status, org_id=org_id, limit=limit)


class ConcurrentPageQuality(FakePageQuality):
    def __init__(self, barrier: ReadBarrier) -> None:
        super().__init__()
        self.barrier = barrier

    async def summary(self, org_id: str, days: int) -> dict[str, Any]:
        await self.barrier.arrive()
        return await super().summary(org_id, days)


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
                page_quality=FakePageQuality(),
            ),
            integrations=SimpleNamespace(database=object()),
        )
    )


def _page_quality_summary(**overrides: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "average_score": 62.4,
        "total_runs": 2,
        "scored_pages": 7,
        "total_pages": 9,
        "passed": 5,
        "needs_revision": 1,
        "awaiting_human": 1,
        "by_metric": [
            {"metric": "detail", "average_score": 100.0, "sample_count": 7},
            {"metric": "quality_score", "average_score": 62.4, "sample_count": 7},
        ],
        "trend": [
            {"date": "2026-09-27", "average_score": 60.0, "run_count": 1},
            {"date": "2026-09-28", "average_score": 65.0, "run_count": 1},
        ],
    }
    summary.update(overrides)
    return summary


def test_page_quality_maps_metrics_to_dimensions_and_trend() -> None:
    evaluation, failed, status = overview._page_quality_evaluation_summary(_page_quality_summary())

    assert evaluation == {
        "average_score": 62.4,
        "trend": 5.0,
        "dimensions": [
            {"name": "detail", "value": 100.0},
            {"name": "quality_score", "value": 62.4},
        ],
    }
    assert failed == 2
    assert status == "Needs review"


def test_page_quality_skips_unscored_metrics() -> None:
    evaluation, _, _ = overview._page_quality_evaluation_summary(
        _page_quality_summary(
            by_metric=[
                {"metric": "detail", "average_score": None, "sample_count": 0},
            ]
        )
    )

    assert evaluation["dimensions"] == []


def test_page_quality_reports_unknown_when_nothing_is_scored() -> None:
    evaluation, failed, status = overview._page_quality_evaluation_summary(
        _page_quality_summary(
            average_score=None,
            scored_pages=0,
            total_pages=0,
            passed=0,
            needs_revision=0,
            awaiting_human=0,
            by_metric=[],
            trend=[],
        )
    )

    assert evaluation == {"average_score": None, "trend": None, "dimensions": []}
    assert failed == 0
    assert status == "Unknown"


def test_page_quality_is_idle_when_every_page_passed() -> None:
    _, _, status = overview._page_quality_evaluation_summary(
        _page_quality_summary(needs_revision=0, awaiting_human=0)
    )

    assert status == "Idle"


def test_page_quality_trend_needs_two_scored_days() -> None:
    evaluation, _, _ = overview._page_quality_evaluation_summary(
        _page_quality_summary(trend=[{"date": "2026-09-28", "average_score": 65.0, "run_count": 1}])
    )

    assert evaluation["trend"] is None


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
                page_quality=FakePageQuality(
                    _page_quality_summary(
                        average_score=85.0,
                        total_runs=2,
                        scored_pages=2,
                        total_pages=2,
                        passed=2,
                        needs_revision=0,
                        awaiting_human=0,
                        by_metric=[
                            {
                                "metric": "correctness",
                                "average_score": 85.0,
                                "sample_count": 2,
                            }
                        ],
                        trend=[
                            {
                                "date": yesterday.date().isoformat(),
                                "average_score": 80.0,
                                "run_count": 1,
                            },
                            {
                                "date": now.date().isoformat(),
                                "average_score": 90.0,
                                "run_count": 1,
                            },
                        ],
                    )
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
    assert snapshot["system"]["evaluations_status"] == "Idle"
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

    async def workflows_for_org(*, org_id: str, db: object) -> list[dict[str, Any]]:
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

    monkeypatch.setattr(overview, "list_github_workflows_record", workflows_for_org, raising=False)

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


@pytest.mark.asyncio
async def test_overview_reads_agent_health_with_one_bulk_org_scoped_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dashboard must not rescan runs and steps once per catalog agent."""
    application = _empty_application()
    agent_runs = FakeAgentRuns()
    application.dependencies.repositories.agent_runs = agent_runs

    async def no_workflows(**_: object) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(overview, "list_github_workflows_record", no_workflows)

    snapshot = await build_overview_snapshot(application, "org-a", 14)

    assert snapshot["system"]["agents_total"] == 3
    assert snapshot["system"]["agents_online"] == 2
    assert agent_runs.calls == [("org-a", 200)]


@pytest.mark.asyncio
async def test_overview_starts_independent_source_reads_concurrently() -> None:
    """Remote source latency must not accumulate until the proxy times out."""
    barrier = ReadBarrier(expected=4)
    application = _empty_application()
    application.dependencies.integrations.database = None
    application.dependencies.repositories.documents = ConcurrentDocuments(barrier)
    application.dependencies.repositories.reviews = ConcurrentReviews(barrier)
    application.dependencies.repositories.page_quality = ConcurrentPageQuality(barrier)

    snapshot = await asyncio.wait_for(
        build_overview_snapshot(application, "org-a", 14),
        timeout=10,
    )

    assert barrier.started == 4
    assert snapshot["summary"]["documentation_total"] == 0
