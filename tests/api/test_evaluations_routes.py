"""Evaluations API: org-scoped list, detail fetch, run trigger."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.evaluations import router
from draftly.integrations.database.evaluations_store import DatabaseEvaluationsStore
from draftly.persistence.repositories.evaluations import EvaluationRepository


class FakeDatabase:
    def __init__(self, evaluations: list[dict[str, Any]] | None = None) -> None:
        self.evaluations = evaluations or []
        self.queries: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *params: Any) -> str:
        self.queries.append((sql, params))
        return "OK"

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        self.queries.append((sql, params))
        eval_id = params[0] if params else None
        return next(
            (e for e in self.evaluations if str(e["id"]) == str(eval_id)),
            None,
        )

    async def fetch_all(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        return list(self.evaluations)


EVAL_ROW = {
    "id": "ev-1",
    "org_id": "org-1",
    "evaluation_type": "documentation",
    "target_id": "auth-guide",
    "score": 96.0,
    "status": "completed",
    "metrics": {"faithfulness": 96, "completeness": 98},
    "failures": [],
    "started_at": "2026-09-02T09:00:00Z",
    "completed_at": "2026-09-02T09:03:00Z",
}


def make_app(db: FakeDatabase | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}

    repo = EvaluationRepository(store=DatabaseEvaluationsStore(client=db))
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(repositories=SimpleNamespace(evaluations=repo))
    )
    return app


def test_list_evaluations_returns_items() -> None:
    db = FakeDatabase(evaluations=[EVAL_ROW])
    app = make_app(db)
    client = TestClient(app)

    resp = client.get("/evaluations")

    assert resp.status_code == 200
    assert resp.json()["items"][0]["id"] == "ev-1"
    sql, params = db.queries[-1]
    assert "org_id" in sql
    assert "org-1" in params


def test_get_evaluation_returns_item_for_own_org() -> None:
    app = make_app(FakeDatabase(evaluations=[EVAL_ROW]))
    client = TestClient(app)

    resp = client.get("/evaluations/ev-1")

    assert resp.status_code == 200
    assert resp.json()["item"]["id"] == "ev-1"
    assert resp.json()["item"]["score"] == 96.0


def test_get_evaluation_404_for_unknown_or_other_org() -> None:
    app = make_app(FakeDatabase(evaluations=[EVAL_ROW]))
    client = TestClient(app)

    assert client.get("/evaluations/missing").status_code == 404

    other_org_db = FakeDatabase(evaluations=[{**EVAL_ROW, "org_id": "org-2"}])
    other_app = make_app(other_org_db)
    assert TestClient(other_app).get("/evaluations/ev-1").status_code == 404


def test_run_evaluations_uses_worker_with_org_and_run_id() -> None:
    captured: dict = {}

    class FakeWorker:
        @property
        def task_runner(self) -> SimpleNamespace:
            return SimpleNamespace(has_task=lambda name: True)

        async def run_task(self, name: str, **kwargs: Any) -> dict:
            captured["name"] = name
            captured.update(kwargs)
            return {"status": "completed", "result": {}}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-42"}
    app.state.draftly = SimpleNamespace(worker=FakeWorker())

    resp = TestClient(app).post("/evaluations/run")

    assert resp.status_code == 200
    assert captured["name"] == "evaluation.loop"
    assert captured["org_id"] == "org-42"
    assert captured["run_id"]
    assert captured["live"] is False


def test_run_evaluations_worker_forwards_live_flag() -> None:
    captured: dict = {}

    class FakeWorker:
        @property
        def task_runner(self) -> SimpleNamespace:
            return SimpleNamespace(has_task=lambda name: True)

        async def run_task(self, name: str, **kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"status": "completed", "result": {}}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-42"}
    app.state.draftly = SimpleNamespace(worker=FakeWorker())

    resp = TestClient(app).post("/evaluations/run?live=true")

    assert resp.status_code == 200
    assert captured["live"] is True


def test_run_evaluations_fallback_registry_uses_org_and_run_id() -> None:
    captured: dict = {}

    async def fake_loop(context, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run_id=kwargs.get("run_id", ""), status="completed")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-42"}
    app.state.draftly = SimpleNamespace(
        worker=None,
        workflows=SimpleNamespace(
            registry=SimpleNamespace(get=lambda name: fake_loop),
            context=None,
        ),
    )
    resp = TestClient(app).post("/evaluations/run")

    assert resp.status_code == 200
    assert captured["org_id"] == "org-42"
    assert captured["run_id"]
    assert captured["live"] is False


def test_run_evaluations_fallback_forwards_live_flag() -> None:
    captured: dict = {}

    async def fake_loop(context, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run_id=kwargs.get("run_id", ""), status="completed")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-42"}
    app.state.draftly = SimpleNamespace(
        worker=None,
        workflows=SimpleNamespace(
            registry=SimpleNamespace(get=lambda name: fake_loop),
            context=None,
        ),
    )
    resp = TestClient(app).post("/evaluations/run?live=true")

    assert resp.status_code == 200
    assert captured["live"] is True


class FakePageQualityRepo:
    """Stands in for PageQualityRepository at the route boundary."""

    def __init__(self) -> None:
        self.summary_calls: list[tuple[str, int]] = []
        self.page_calls: list[dict[str, Any]] = []

    async def summary(self, org_id: str, days: int) -> dict[str, Any]:
        self.summary_calls.append((org_id, days))
        return {
            "average_score": 62.0,
            "total_runs": 2,
            "scored_pages": 7,
            "total_pages": 9,
            "passed": 5,
            "needs_revision": 1,
            "awaiting_human": 1,
            "by_metric": [{"metric": "detail", "average_score": 100.0, "sample_count": 7}],
            "trend": [{"date": "2026-09-28", "average_score": 62.0, "run_count": 1}],
        }

    async def pages(
        self, org_id: str, *, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        self.page_calls.append({"org_id": org_id, "limit": limit, "cursor": cursor})
        return (
            [
                {
                    "page_id": "p1",
                    "path": "docs/a.md",
                    "status": "revision_required",
                    "score": 40.0,
                    "run_id": "run-1",
                    "updated_at": "2026-09-28T00:00:00Z",
                }
            ],
            1,
            None,
        )


def make_page_quality_app() -> tuple[FastAPI, FakePageQualityRepo]:
    repo = FakePageQualityRepo()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                evaluations=EvaluationRepository(
                    store=DatabaseEvaluationsStore(client=FakeDatabase())
                ),
                page_quality=repo,
            )
        )
    )
    return app, repo


def test_summary_serves_page_quality_not_harness_aggregates() -> None:
    app, repo = make_page_quality_app()
    client = TestClient(app)

    resp = client.get("/evaluations/summary", params={"days": 7})

    assert resp.status_code == 200
    body = resp.json()
    assert body["scored_pages"] == 7
    assert body["total_pages"] == 9
    assert body["by_metric"][0]["metric"] == "detail"
    assert repo.summary_calls == [("org-1", 7)]


def test_summary_rejects_unsupported_days() -> None:
    app, _ = make_page_quality_app()
    assert TestClient(app).get("/evaluations/summary", params={"days": 3}).status_code == 422


def test_pages_endpoint_is_org_scoped_and_forwards_cursor() -> None:
    app, repo = make_page_quality_app()
    client = TestClient(app)

    resp = client.get("/evaluations/pages", params={"limit": 20, "cursor": "0.3:p0:run-9"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["path"] == "docs/a.md"
    assert body["items"][0]["score"] == 40.0
    assert repo.page_calls[0] == {"org_id": "org-1", "limit": 20, "cursor": "0.3:p0:run-9"}


def test_pages_literal_route_is_not_swallowed_by_evaluation_id() -> None:
    # /{evaluation_id} is greedy; /pages must be declared before it.
    app, _ = make_page_quality_app()
    resp = TestClient(app).get("/evaluations/pages")

    assert resp.status_code == 200
    assert "items" in resp.json()


def test_pages_maps_a_malformed_cursor_to_422() -> None:
    class BadCursorRepo(FakePageQualityRepo):
        async def pages(self, org_id: str, *, limit: int = 50, cursor=None):  # type: ignore[override]
            raise ValueError("invalid cursor: 'bogus'")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-1"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(repositories=SimpleNamespace(page_quality=BadCursorRepo()))
    )
    assert TestClient(app).get("/evaluations/pages", params={"cursor": "bogus"}).status_code == 422
