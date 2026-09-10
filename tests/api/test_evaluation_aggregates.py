from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.evaluations import router


class AggregateRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_runs(self, **kwargs: Any):
        self.calls.append(("list_runs", kwargs))
        return ([{
            "id": "evaluation-1",
            "org_id": "org-1",
            "run_id": "run-1",
            "evaluation_type": "documentation",
            "score": 92,
            "passed": True,
            "status": "completed",
            "metrics": {"cases": 2, "passed": 2, "failed": 0},
            "started_at": "2026-09-10T08:00:00+00:00",
            "completed_at": "2026-09-10T08:00:02+00:00",
        }], 1, "next-run")

    async def get_run_detail(self, **kwargs: Any):
        self.calls.append(("get_run_detail", kwargs))
        return {
            "summary": {
                "id": "evaluation-1",
                "org_id": "org-1",
                "run_id": "run-1",
                "evaluation_type": "documentation",
                "score": 92,
                "passed": True,
                "status": "passed",
                "metrics": {"cases": 1, "passed": 1, "failed": 0},
                "started_at": "2026-09-10T08:00:00+00:00",
                "completed_at": "2026-09-10T08:00:02+00:00",
            },
            "cases": [{
                "id": "case-result-1",
                "evaluation_id": "evaluation-1",
                "run_id": "run-1",
                "dataset": "documentation",
                "case_id": "oauth-auth",
                "metric": "expected_contains",
                "score": 100,
                "passed": True,
            }],
            "next_cases_cursor": "next-case",
            "detail_available": True,
        }

    async def aggregate_summary(self, **kwargs: Any):
        self.calls.append(("aggregate_summary", kwargs))
        return {
            "window_days": kwargs["days"],
            "average_score": None,
            "total_runs": 0,
            "passed_runs": 0,
            "failed_runs": 0,
            "total_cases": 0,
            "pass_rate": None,
            "trend": [],
            "by_metric": [],
        }

    def catalog(self):
        self.calls.append(("catalog", {}))
        return {
            "datasets": [{"name": "documentation", "case_count": 2}],
            "evaluators": [{"key": "expected_contains", "display_name": "Coverage"}],
        }


def make_app(repo: AggregateRepo) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(repositories=SimpleNamespace(evaluations=repo))
    )
    return app


def test_list_runs_returns_cursor_page_and_forwards_filters() -> None:
    repo = AggregateRepo()
    response = TestClient(make_app(repo)).get(
        "/evaluations?evaluation_type=documentation&limit=10&cursor=previous"
    )

    assert response.status_code == 200
    assert response.json()["next_cursor"] == "next-run"
    assert response.json()["items"][0]["status"] == "passed"
    assert repo.calls[-1] == (
        "list_runs",
        {
            "org_id": "org-1",
            "evaluation_type": "documentation",
            "limit": 10,
            "cursor": "previous",
        },
    )


def test_run_detail_returns_cases_and_catalog_metadata() -> None:
    repo = AggregateRepo()
    response = TestClient(make_app(repo)).get(
        "/evaluations/runs/run-1?cases_limit=25&cases_cursor=case-prev"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["run_id"] == "run-1"
    assert body["cases"][0]["case_id"] == "oauth-auth"
    assert body["datasets"] == []
    assert repo.calls[-1] == (
        "get_run_detail",
        {
            "org_id": "org-1",
            "run_id": "run-1",
            "cases_limit": 25,
            "cases_cursor": "case-prev",
        },
    )


def test_empty_summary_window_is_explicitly_null() -> None:
    repo = AggregateRepo()
    response = TestClient(make_app(repo)).get("/evaluations/summary?days=14")

    assert response.status_code == 200
    assert response.json()["average_score"] is None
    assert response.json()["pass_rate"] is None
    assert repo.calls[-1][1] == {"org_id": "org-1", "days": 14}


def test_catalog_returns_dataset_and_evaluator_definitions() -> None:
    response = TestClient(make_app(AggregateRepo())).get("/evaluations/catalog")

    assert response.status_code == 200
    assert response.json()["datasets"][0]["name"] == "documentation"
    assert response.json()["evaluators"][0]["key"] == "expected_contains"
