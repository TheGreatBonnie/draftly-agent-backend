"""Integration management route authorization and tenant boundaries."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes import integrations


def _client(role="admin"):
    app = FastAPI()
    app.include_router(integrations.router)
    app.dependency_overrides[get_verified_token] = lambda: {
        "user_id": "user_1",
        "org_id": "org_1",
        "org_role": role,
    }
    state = MagicMock()
    db = state.dependencies.integrations.database

    async def rows(query, org_id):
        assert org_id == "org_1"
        if "FROM github_installations" in query:
            return [{"id": "gh_1", "installation_id": 51, "github_org": "acme", "repositories": []}]
        if "FROM slack_installations" in query:
            return [{"id": "slack_1", "team_id": "T1", "team_name": "Acme"}]
        return []

    db.fetch_all = AsyncMock(side_effect=rows)
    db.fetch_one = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    state.dashboard_broadcaster = AsyncMock()
    app.state.draftly = state
    app.state.dashboard_broadcaster = AsyncMock()
    return app, TestClient(app), db


def test_list_is_org_scoped_and_does_not_claim_unchecked_health():
    app, client, db = _client()
    with client:
        response = client.get("/integrations")
    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["providers"]] == ["github", "slack", "discord"]
    assert [item["id"] for item in payload["connections"]] == ["gh_1", "slack_1"]
    assert all(item["health"]["status"] == "unknown" for item in payload["connections"])
    assert all(call.args[1] == "org_1" for call in db.fetch_all.await_args_list)


def test_unknown_provider_returns_404():
    _, client, _ = _client()
    with client:
        response = client.get("/integrations/not-real")
    assert response.status_code == 404


def test_disconnect_requires_admin_and_owned_connection():
    _, viewer, viewer_db = _client("member")
    with viewer:
        denied = viewer.delete("/integrations/github/gh_1")
    assert denied.status_code == 403
    viewer_db.execute.assert_not_awaited()

    app, admin, db = _client()
    with admin:
        missing = admin.delete("/integrations/github/someone-else")
        success = admin.delete("/integrations/github/gh_1")
    assert missing.status_code == 404
    assert success.status_code == 200
    assert db.execute.await_args_list[0].args[1:] == ("gh_1", "org_1")
    app.state.dashboard_broadcaster.broadcast.assert_awaited_once_with(
        "org_1", "integration:changed", {"provider": "github"}
    )
