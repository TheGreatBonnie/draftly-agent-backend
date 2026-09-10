from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.workflow_runs import router


def run_row() -> dict:
    return {
        "id": "run-1",
        "definition_id": "00000000-0000-0000-0000-000000000001",
        "org_id": "org-1",
        "source": "manual",
        "source_event_id": "request-1",
        "event_type": "manual.workflow",
        "title": "PR docs",
        "repository": "acme/docs",
        "actor": "user-1",
        "target": {},
        "status": "running",
        "current_stage": "research",
        "stage_states": {"research": "running"},
        "input": {},
        "output": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "created_at": None,
        "updated_at": None,
    }


def make_app(role: str = "member") -> tuple[FastAPI, SimpleNamespace]:
    runs = SimpleNamespace(
        get=AsyncMock(),
        list=AsyncMock(return_value=([run_row()], 1, None)),
        list_for_definition=AsyncMock(return_value=([run_row()], 1, None)),
        list_steps=AsyncMock(return_value=[]),
        list_artifacts=AsyncMock(return_value=[]),
        start_or_get_idempotent=AsyncMock(return_value=run_row()),
        update_state=AsyncMock(),
    )
    definitions = SimpleNamespace(
        get=AsyncMock(return_value={"id": "def-1", "workflow_key": "github_pr", "status": "active"})
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {
        "org_id": "org-1",
        "user_id": "user-1",
        "org_role": role,
    }
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(workflow_runs=runs, workflow_definitions=definitions)
        ),
        settings=SimpleNamespace(rq_enabled=False),
        worker=None,
    )
    return app, SimpleNamespace(runs=runs, definitions=definitions)


def test_run_detail_and_steps_are_org_scoped() -> None:
    app, repos = make_app()
    repos.runs.get.return_value = run_row()
    client = TestClient(app)

    response = client.get("/workflow-runs/run-1")
    steps = client.get("/workflow-runs/run-1/steps")

    assert response.status_code == 200
    assert response.json()["run"]["id"] == "run-1"
    assert steps.status_code == 200
    assert repos.runs.get.await_args.kwargs["org_id"] == "org-1"


def test_unknown_run_returns_404() -> None:
    app, repos = make_app()
    repos.runs.get.return_value = None

    assert TestClient(app).get("/workflow-runs/foreign").status_code == 404


def test_manual_run_requires_editor_and_uses_idempotency_header() -> None:
    app, repos = make_app(role="editor")
    client = TestClient(app)

    response = client.post(
        "/workflow-runs",
        json={"definition_id": "def-1", "title": "PR docs"},
        headers={"Idempotency-Key": "request-1"},
    )

    assert response.status_code == 201
    repos.runs.start_or_get_idempotent.assert_awaited_once()
    assert repos.runs.start_or_get_idempotent.await_args.kwargs["source_event_id"] == "request-1"


def test_pending_intervention_is_a_valid_run_status() -> None:
    app, repos = make_app()
    row = run_row()
    row["status"] = "pending_intervention"
    repos.runs.list.return_value = ([row], 1, None)

    response = TestClient(app).get("/workflow-runs?status=pending_intervention")

    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "pending_intervention"
    assert repos.runs.list.await_args.kwargs["status"] == "pending_intervention"
