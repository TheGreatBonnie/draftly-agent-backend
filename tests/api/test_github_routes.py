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
    monkeypatch.setattr(github.settings, "frontend_url", "https://app.example.com")


def test_install_url_sets_return_to_cookie_and_returns_direct_github_url(app, pinned_settings):
    with TestClient(app) as client:
        response = client.get("/github/install-url?return_to=/onboarding/github")
    assert response.status_code == 200
    # Pre-0ac50e8 contract: the install URL points straight at github.com, so the
    # return-to cookie is planted on the caller's own origin and GitHub's
    # setup-callback (registered on that same origin) receives it.
    assert response.json()["install_url"] == "https://github.com/apps/draftly/installations/new"
    assert 'gh_install_return_to="/onboarding/github"' in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Path=/api" in response.headers["set-cookie"]


def test_install_url_rejects_external_return_path(app, pinned_settings):
    with TestClient(app) as client:
        response = client.get("/github/install-url?return_to=https://evil.example")
    assert response.status_code == 400


def test_setup_callback_redirects_with_return_to_cookie(app, pinned_settings):
    with TestClient(app) as client:
        client.cookies.set("gh_install_return_to", "/onboarding/github")
        response = client.get(
            "/github/setup-callback?installation_id=42&setup_action=install",
            follow_redirects=False,
        )
    assert response.status_code == 307
    assert response.headers["location"] == (
        f"{github.settings.frontend_url}/onboarding/github?installation_id=42"
    )
    assert "gh_install_return_to=" in response.headers["set-cookie"]


def test_setup_callback_falls_back_without_cookie(app, pinned_settings):
    """No cookie must still redirect, not 400: the endpoint is GitHub's Setup URL
    and GitHub will not resend the browser if we reject the request."""
    with TestClient(app) as client:
        response = client.get(
            "/github/setup-callback?installation_id=42&setup_action=install",
            follow_redirects=False,
        )
    assert response.status_code == 307
    assert response.headers["location"] == (
        f"{github.settings.frontend_url}/integrations/github?installation_id=42"
    )


def test_setup_callback_rejects_external_return_path(app, pinned_settings):
    with TestClient(app) as client:
        client.cookies.set("gh_install_return_to", "https://evil.example")
        response = client.get(
            "/github/setup-callback?installation_id=42&setup_action=install",
            follow_redirects=False,
        )
    assert response.status_code == 307
    assert response.headers["location"] == (
        f"{github.settings.frontend_url}/integrations/github?installation_id=42"
    )


def test_lists_only_caller_org_installations(app):
    repos = app.state.draftly.dependencies.repositories
    repos.github_installations.list_by_org = AsyncMock(return_value=[{"installation_id": 42}])
    with TestClient(app) as client:
        response = client.get("/github/installations")
    assert response.status_code == 200
    repos.github_installations.list_by_org.assert_awaited_once_with("org_1")
