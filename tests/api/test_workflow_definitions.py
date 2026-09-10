from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.workflow_templates import router as template_router
from draftly.app.api.routes.workflows import router


def make_app(*, role: str = "admin") -> tuple[FastAPI, SimpleNamespace]:
    definitions = SimpleNamespace(
        list=AsyncMock(),
        get=AsyncMock(),
        create=AsyncMock(),
        update=AsyncMock(),
        set_status=AsyncMock(),
        summary=AsyncMock(return_value={"definitions": {}, "runs": {}}),
    )
    templates = SimpleNamespace(list=AsyncMock(), get=AsyncMock(), create=AsyncMock())
    repositories = SimpleNamespace(
        workflow_definitions=definitions,
        workflow_templates=templates,
        workflow_runs=SimpleNamespace(),
        jobs=AsyncMock(),
        workflow_events=AsyncMock(),
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(template_router)
    app.dependency_overrides[get_verified_token] = lambda: {
        "org_id": "org-1",
        "user_id": "user-1",
        "org_role": role,
    }
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            integrations=SimpleNamespace(database=AsyncMock()),
            repositories=repositories,
        )
    )
    return app, SimpleNamespace(definitions=definitions, templates=templates)


def definition_row() -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "org_id": "org-1",
        "name": "PR docs",
        "slug": "pr-docs",
        "description": None,
        "workflow_key": "github_pr",
        "status": "draft",
        "version": 1,
        "trigger_config": {},
        "condition_config": {},
        "agent_config": {},
        "repository_config": {},
        "evaluation_config": {},
        "review_config": {},
        "delivery_config": {},
        "created_by": "user-1",
        "created_at": None,
        "updated_at": None,
    }


def test_definition_list_returns_items_summary_and_cursor() -> None:
    app, repos = make_app(role="member")
    repos.definitions.list.return_value = ([definition_row()], 1, "next")
    client = TestClient(app)

    response = client.get("/workflows?status=draft&limit=201&days=30")

    assert response.status_code == 200
    assert response.json()["items"][0]["slug"] == "pr-docs"
    assert response.json()["next_cursor"] == "next"
    repos.definitions.list.assert_awaited_once_with(
        org_id="org-1", status="draft", workflow_key=None, limit=201, cursor=None
    )
    repos.definitions.summary.assert_awaited_once_with(org_id="org-1", days=30)


def test_definition_unknown_id_is_not_enumerable() -> None:
    app, repos = make_app()
    repos.definitions.get.return_value = None
    client = TestClient(app)

    response = client.get("/workflows/foreign-id")

    assert response.status_code == 404


def test_definition_create_requires_editor_role() -> None:
    app, repos = make_app(role="member")
    client = TestClient(app)

    response = client.post(
        "/workflows",
        json={"name": "PR docs", "slug": "pr-docs", "workflow_key": "github_pr"},
    )

    assert response.status_code == 403
    repos.definitions.create.assert_not_awaited()


def test_definition_create_uses_verified_org_and_user() -> None:
    app, repos = make_app(role="editor")
    repos.definitions.create.return_value = definition_row()
    client = TestClient(app)

    response = client.post(
        "/workflows",
        json={"name": "PR docs", "slug": "pr-docs", "workflow_key": "github_pr"},
    )

    assert response.status_code == 201
    repos.definitions.create.assert_awaited_once()
    assert repos.definitions.create.await_args.kwargs["org_id"] == "org-1"
    assert repos.definitions.create.await_args.kwargs["created_by"] == "user-1"


def test_templates_are_visible_to_members() -> None:
    app, repos = make_app(role="member")
    repos.templates.list.return_value = [
        {
            "id": "template-1",
            "org_id": None,
            "slug": "github-pr",
            "name": "GitHub PR",
            "description": None,
            "workflow_key": "github_pr",
            "defaults": {},
            "is_system": True,
            "created_at": None,
            "updated_at": None,
        }
    ]
    response = TestClient(app).get("/workflow-templates")

    assert response.status_code == 200
    assert response.json()["items"][0]["is_system"] is True


def test_template_instantiation_creates_a_draft_in_verified_org() -> None:
    app, repos = make_app(role="editor")
    repos.templates.get.return_value = {
        "id": "template-1",
        "org_id": None,
        "slug": "github-pr",
        "name": "GitHub PR",
        "description": "Defaults",
        "workflow_key": "github_pr",
        "defaults": {"name": "PR docs", "slug": "pr-docs"},
        "is_system": True,
        "created_at": None,
        "updated_at": None,
    }
    repos.definitions.create.return_value = definition_row()

    response = TestClient(app).post(
        "/workflow-templates/template-1/instantiate",
        json={"name": "Docs from template", "slug": "docs-from-template"},
    )

    assert response.status_code == 201
    args = repos.definitions.create.await_args.kwargs
    assert args["org_id"] == "org-1"
    assert args["payload"].name == "Docs from template"
    assert args["payload"].status == "draft"
