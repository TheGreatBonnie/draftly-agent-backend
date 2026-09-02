"""Live workflows list: github_workflows identity joined to jobs/events state."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.workflows import router


def make_workflows_app(mock_rows: list[dict]) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "o-1", "sub": "u-1"}

    # Mock db and list_github_workflows_record via patching the import inside route?
    # Instead we mock dependencies layer and let the route call the repo function,
    # but we patch the repo function itself.
    from types import SimpleNamespace

    mock_db = MagicMock()
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            integrations=SimpleNamespace(database=mock_db),
            repositories=SimpleNamespace(jobs=MagicMock(), workflow_events=MagicMock()),
        ),
        workflows=SimpleNamespace(event_bus=MagicMock()),
    )
    # Provide redis_tickets stub not needed for list endpoint
    return app


def test_list_workflows_returns_items_with_expected_shape(monkeypatch) -> None:
    mock_rows = [
        {
            "run_id": "ev-1",
            "title": "OAuth Documentation Update",
            "target_doc": None,
            "trigger_label": "PR #482",
            "status": "running",
            "current_stage": "research",
            "stages": ["done", "running", "queued", "queued", "queued", "queued", "queued", "queued", "queued"],
            "current_stage_color": "blue",
            "time": "18.4s",
        }
    ]

    async def fake_list(org_id: str, db=None):
        assert org_id == "o-1"
        return mock_rows

    import draftly.persistence.repositories.github as github_mod

    monkeypatch.setattr(github_mod, "list_github_workflows_record", fake_list)

    app = make_workflows_app(mock_rows)
    client = TestClient(app)
    resp = client.get("/workflows")
    # router is mounted at /workflows, GET "" maps to /workflows (with or without trailing slash)
    # FastAPI may redirect /workflows -> /workflows/ ? Try both.
    if resp.status_code == 307:
        resp = client.get("/workflows/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["run_id"] == "ev-1"
    assert item["title"] == "OAuth Documentation Update"
    assert item["trigger_label"] == "PR #482"
    assert item["status"] == "running"
    assert item["current_stage"] == "research"
    assert isinstance(item["stages"], list)
    assert item["time"] == "18.4s"
    assert "target_doc" in item


def test_list_workflows_empty(monkeypatch) -> None:
    async def fake_list(org_id: str, db=None):
        return []

    import draftly.persistence.repositories.github as github_mod

    monkeypatch.setattr(github_mod, "list_github_workflows_record", fake_list)

    app = make_workflows_app([])
    client = TestClient(app)
    resp = client.get("/workflows")
    if resp.status_code == 307:
        resp = client.get("/workflows/")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
