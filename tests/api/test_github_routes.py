"""Tests for organization-bound GitHub installation setup."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes import github


@pytest.fixture()
def app():
    api = FastAPI()
    api.include_router(github.router)
    api.dependency_overrides[get_verified_token] = lambda: {
        "user_id": "user_1",
        "org_id": "org_1",
        "org_role": "admin",
    }
    state = MagicMock()
    state.dependencies.integrations.database.fetch_one = AsyncMock(
        return_value={"nonce": "state_1"}
    )
    state.dependencies.integrations.database.execute = AsyncMock()
    state.dependencies.repositories.github_installations.list_by_org = AsyncMock(return_value=[])
    api.state.draftly = state
    return api


@pytest.fixture()
def pinned_settings(monkeypatch):
    monkeypatch.setattr(github.settings, "github_app_slug", "draftly")
    monkeypatch.setattr(github.settings, "github_client_id", "client_1")
    monkeypatch.setattr(github.settings, "github_client_secret", "secret_1")


def test_install_url_uses_server_side_one_time_state(app, pinned_settings):
    with TestClient(app) as client:
        response = client.get("/github/install-url?return_to=/integrations/github")
    assert response.status_code == 200
    assert "/github/setup-start?state=" in response.json()["install_url"]
    db = app.state.draftly.dependencies.integrations.database
    assert db.execute.await_args.args[3:6] == ("org_1", "user_1", "/integrations/github")


def test_install_url_rejects_external_return_path(app, pinned_settings):
    with TestClient(app) as client:
        response = client.get("/github/install-url?return_to=https://evil.example")
    assert response.status_code == 400


def test_setup_start_sets_backend_cookie(app, pinned_settings):
    with TestClient(app) as client:
        response = client.get("/github/setup-start?state=state_1", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://github.com/apps/draftly/installations/new"
    assert "github_setup_state=state_1" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_setup_callback_rejects_unbound_installation(app, pinned_settings):
    with TestClient(app) as client:
        response = client.get(
            "/github/setup-callback?installation_id=42&setup_action=install",
            follow_redirects=False,
        )
    assert response.status_code == 400


def test_setup_callback_requires_user_authorization(app, pinned_settings):
    with TestClient(app) as client:
        client.cookies.set("github_setup_state", "state_1")
        response = client.get(
            "/github/setup-callback?installation_id=42&setup_action=install",
            follow_redirects=False,
        )
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    assert "state=state_1" in response.headers["location"]
    db = app.state.draftly.dependencies.integrations.database
    db.execute.assert_awaited_with(
        "UPDATE integration_oauth_states SET installation_id = $1 WHERE nonce = $2",
        42,
        "state_1",
    )


def test_lists_only_caller_org_installations(app):
    repos = app.state.draftly.dependencies.repositories
    repos.github_installations.list_by_org = AsyncMock(return_value=[{"installation_id": 42}])
    with TestClient(app) as client:
        response = client.get("/github/installations")
    assert response.status_code == 200
    repos.github_installations.list_by_org.assert_awaited_once_with("org_1")
