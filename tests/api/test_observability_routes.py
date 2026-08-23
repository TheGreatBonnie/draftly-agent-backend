"""Routing/performance/jobs read endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.observability import router

DECISION = {
    "request_id": "evt-1",
    "task_type": "writer",
    "selected_model": "claude-haiku",
    "provider": "bedrock",
    "score": 4.2,
    "latency_ms": 812.0,
    "success": True,
}

PERF = {"model_name": "claude-haiku", "task_type": "writer", "success_rate": 0.97}


class FakeRepos:
    routing = SimpleNamespace(recent=None)
    performance = SimpleNamespace(all=None)
    jobs = SimpleNamespace(list_active=None)

    @staticmethod
    async def _routing_recent(limit: int = 100) -> list[dict[str, Any]]:
        return [DECISION]

    @staticmethod
    async def _perf_all() -> list[dict[str, Any]]:
        return [PERF]

    @staticmethod
    async def _jobs_active() -> list[dict[str, Any]]:
        return [{"job_id": "j-1"}]


def make_app() -> FastAPI:
    repos = FakeRepos()
    repos.routing.recent = FakeRepos._routing_recent
    repos.performance.all = FakeRepos._perf_all
    repos.jobs.list_active = FakeRepos._jobs_active

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(dependencies=SimpleNamespace(repositories=repos))
    return app


def test_routing_decisions_clamps_limit() -> None:
    client = TestClient(make_app())
    resp = client.get("/observability/routing-decisions", params={"limit": 10_000})
    assert resp.status_code == 200
    assert resp.json()["items"] == [DECISION]


def test_model_performance_lists_aggregates() -> None:
    client = TestClient(make_app())
    resp = client.get("/observability/model-performance")
    assert resp.status_code == 200
    assert resp.json()["items"] == [PERF]


def test_jobs_active_list() -> None:
    client = TestClient(make_app())
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json()["items"] == [{"job_id": "j-1"}]
