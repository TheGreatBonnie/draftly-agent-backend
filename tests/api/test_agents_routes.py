"""Agents catalog API: definitions, tool mapping, live status/history derivation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api import routes as route_modules
from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.agents import router


class FakeRunsRepo:
    def __init__(self, runs: list[dict[str, Any]] | None = None,
                 steps: list[dict[str, Any]] | None = None) -> None:
        self.runs = runs or []
        self.steps = steps or []
        self.last_list_runs_kwargs: dict[str, Any] = {}

    async def list_runs(self, *, org_id: str | None = None, status: str | None = None,
                        limit: int = 50) -> list[dict[str, Any]]:
        self.last_list_runs_kwargs = {"org_id": org_id, "status": status, "limit": limit}
        return self.runs

    async def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        return self.steps


def make_app(runs_repo: FakeRunsRepo | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(agent_runs=runs_repo or FakeRunsRepo())
        )
    )
    return app


def test_list_agents_returns_catalog_with_expected_roles() -> None:
    app = make_app()
    client = TestClient(app)
    resp = client.get("/agents")
    assert resp.status_code == 200
    roles = {a["role"] for a in resp.json()["agents"]}
    assert "classifier" in roles
    assert "context_agent" in roles
    assert "writer_agent" in roles
    assert "research_swarm_factory" in roles


def test_agents_include_name_description_and_tools() -> None:
    app = make_app()
    client = TestClient(app)
    agents = client.get("/agents").json()["agents"]
    by_role = {a["role"]: a for a in agents}
    writer = by_role["writer_agent"]
    assert writer["name"] == "doc_writer"
    assert writer["description"]
    assert isinstance(writer["tools"], list)
    assert all(isinstance(t, str) for t in writer["tools"])


def test_agent_status_and_history_derived_from_run_steps() -> None:
    runs = [{"run_id": "evt-1", "event_type": "pull_request.opened",
             "status": "completed", "source": "github", "org_id": "org-1"}]
    steps = [
        {"seq": 1, "kind": "node", "name": "classify", "status": "completed",
         "duration_ms": 120, "detail": {}},
        {"seq": 2, "kind": "node", "name": "update", "status": "failed",
         "duration_ms": 500, "detail": {}},
    ]
    repo = FakeRunsRepo(runs=runs, steps=steps)
    app = make_app(repo)
    client = TestClient(app)
    agents = client.get("/agents").json()["agents"]
    writer = next(a for a in agents if a["name"] == "doc_writer")
    assert writer["status"] == "failed"
    assert writer["history"]
    assert repo.last_list_runs_kwargs.get("org_id") == "org-1"


def test_agents_status_defaults_gracefully_when_no_runs() -> None:
    app = make_app(FakeRunsRepo(runs=[], steps=[]))
    client = TestClient(app)
    agents = client.get("/agents").json()["agents"]
    for a in agents:
        assert a["status"] in ("idle", "running", "completed", "failed")
        assert a["history"] == []


def test_build_agent_summaries_reuses_the_catalog_response_shape() -> None:
    app = make_app(FakeRunsRepo(runs=[], steps=[]))
    build_agent_summaries = getattr(route_modules.agents, "build_agent_summaries", None)

    assert callable(build_agent_summaries)
    agents = asyncio.run(build_agent_summaries(app, org_id="org-1"))

    assert agents
    assert agents[0]["role"] == "classifier"
    assert set(agents[0]) == {
        "role",
        "name",
        "description",
        "surface",
        "tools",
        "status",
        "activity",
        "history",
    }
