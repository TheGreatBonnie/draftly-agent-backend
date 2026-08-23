"""Metrics endpoints shape + evaluations org pass-through."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.observability.metrics import metrics


def build_app() -> FastAPI:
    from draftly.app.api.routes.metrics import router as metrics_router

    app = FastAPI()
    app.include_router(metrics_router)
    return app


def test_prometheus_exposition_contains_counter() -> None:
    metrics.increment("test_metric_total")
    client = TestClient(build_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "test_metric_total" in resp.text


def test_snapshot_returns_registry_shape() -> None:
    metrics.set_gauge("test_gauge", 3.5)
    client = TestClient(build_app())
    resp = client.get("/metrics/snapshot")
    body = resp.json()
    assert set(body.keys()) == {"counters", "gauges", "timings"}
    assert body["gauges"]["test_gauge"] == 3.5


def test_evaluations_route_passes_token_org() -> None:
    from types import SimpleNamespace

    seen: dict = {}

    class FakeEvalRepo:
        async def search(self, *, org_id, evaluation_type=None, limit=50):
            seen["org_id"] = org_id
            seen["limit"] = limit
            return []

    from draftly.app.api.routes.evaluations import router as eval_router

    app = FastAPI()
    app.include_router(eval_router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-77"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(evaluations=FakeEvalRepo())
        )
    )

    client = TestClient(app)
    resp = client.get("/evaluations", params={"limit": 500})

    assert resp.status_code == 200
    assert seen["org_id"] == "org-77"
    assert seen["limit"] == 200
