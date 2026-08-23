"""Runs audit API: org scoping, limit clamping, step listing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.runs import router


class FakeDatabase:
    def __init__(self, runs: list[dict[str, Any]] | None = None) -> None:
        self.runs = runs or []
        self.queries: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *params: Any) -> str:
        self.queries.append((sql, params))
        return "OK"

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        self.queries.append((sql, params))
        run_id = params[0] if params else None
        return next((r for r in self.runs if r["run_id"] == run_id), None)

    async def fetch_all(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        return list(self.runs)


RUN_ROW = {
    "run_id": "evt-1",
    "source": "github",
    "event_type": "pull_request.opened",
    "org_id": "org-1",
    "status": "completed",
}


def make_app(db: FakeDatabase | None = None) -> FastAPI:
    from draftly.persistence.repositories.agent_runs import AgentRunsRepository

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}

    repo = AgentRunsRepository(database=db)
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(repositories=SimpleNamespace(agent_runs=repo))
    )
    return app


def test_list_runs_filters_by_token_org() -> None:
    db = FakeDatabase(runs=[RUN_ROW])
    app = make_app(db)
    client = TestClient(app)

    resp = client.get("/runs")

    assert resp.status_code == 200
    assert resp.json()["items"][0]["run_id"] == "evt-1"
    # org clause is bound from the token, never the client
    sql, params = db.queries[-1]
    assert "org_id" in sql
    assert "org-1" in params


def test_get_run_404_for_unknown_or_other_org() -> None:
    app = make_app(FakeDatabase(runs=[RUN_ROW]))
    client = TestClient(app)

    assert client.get("/runs/missing").status_code == 404

    other_org_db = FakeDatabase(runs=[{**RUN_ROW, "org_id": "org-2"}])
    other_app = make_app(other_org_db)
    assert TestClient(other_app).get("/runs/evt-1").status_code == 404


def test_limit_clamped_to_200() -> None:
    db = FakeDatabase(runs=[])
    app = make_app(db)
    client = TestClient(app)

    resp = client.get("/runs", params={"limit": 9999})

    assert resp.status_code == 200
    _, params = db.queries[-1]
    assert int(params[-1]) <= 200


def test_steps_require_known_run_and_return_ordered_items() -> None:
    class SteppedDb(FakeDatabase):
        async def fetch_all(self, sql: str, *params: Any) -> list[dict[str, Any]]:
            if "agent_steps" in sql:
                return [
                    {"seq": 1, "kind": "node", "name": "classify", "status": "completed"},
                    {"seq": 2, "kind": "node", "name": "writer", "status": "failed"},
                ]
            return await super().fetch_all(sql, *params)

    stepped = SteppedDb(runs=[RUN_ROW])
    stepped.runs = [RUN_ROW]
    app = make_app(stepped)
    client = TestClient(app)

    missing = client.get("/runs/missing/steps")
    assert missing.status_code == 404

    found = client.get("/runs/evt-1/steps")
    assert found.status_code == 200
    items = found.json()["items"]
    assert [s["seq"] for s in items] == [1, 2]
