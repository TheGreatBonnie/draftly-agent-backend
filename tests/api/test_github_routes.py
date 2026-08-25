"""Tests for GitHub App setup-redirect routing (return-to cookie handshake)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.routes import github


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(github.router)
    from draftly.app.api.auth import get_verified_token

    app.dependency_overrides[get_verified_token] = lambda: {
        "sub": "tester",
        "org_id": "test-org",
    }
    return TestClient(app)


@pytest.fixture()
def pinned_settings(monkeypatch):
    """Pin Settings attributes on the module-level object the route reads."""
    monkeypatch.setattr(github.settings, "github_app_slug", "draftly")
    monkeypatch.setattr(github.settings, "frontend_url", "http://testfrontend")


class TestInstallUrl:
    def test_returns_documented_request_page_url(self, client, pinned_settings):
        res = client.get("/github/install-url")
        assert res.status_code == 200
        assert res.json()["install_url"] == (
            "https://github.com/apps/draftly/installations/new"
        )

    def test_valid_return_to_plants_cookie(self, client, pinned_settings):
        res = client.get("/github/install-url?return_to=/onboarding/github")
        assert res.status_code == 200
        cookie = res.headers["set-cookie"]
        assert 'gh_install_return_to="/onboarding/github"' in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Path=/api" in cookie
        assert "Max-Age=600" in cookie

    def test_no_return_to_means_no_cookie(self, client, pinned_settings):
        res = client.get("/github/install-url")
        assert res.status_code == 200
        assert "set-cookie" not in res.headers

    def test_off_allowlist_return_to_rejected(self, client, pinned_settings):
        res = client.get("/github/install-url?return_to=https://evil.example")
        assert res.status_code == 400


class TestSetupCallback:
    COOKIE = {"cookies": {"gh_install_return_to": "/onboarding/github"}}

    def test_cookie_routes_back_to_onboarding_with_installation_id(
        self, client, pinned_settings
    ):
        res = client.get(
            "/github/setup-callback?installation_id=42&setup_action=install",
            follow_redirects=False,
            **self.COOKIE,
        )
        assert res.status_code == 307
        location = res.headers["location"]
        assert location == (
            "http://testfrontend/onboarding/github?installation_id=42"
        )
        # Cookie consumed after use (Starlette sets empty value + Max-Age=0).
        set_cookie = res.headers.get("set-cookie", "")
        assert "gh_install_return_to=" in set_cookie
        assert "Max-Age=0" in set_cookie

    def test_missing_cookie_falls_back_to_integrations(self, client, pinned_settings):
        res = client.get(
            "/github/setup-callback?installation_id=42",
            follow_redirects=False,
        )
        assert res.status_code == 307
        assert res.headers["location"] == (
            "http://testfrontend/integrations/github?installation_id=42"
        )

    def test_unknown_cookie_value_falls_back(self, client, pinned_settings):
        res = client.get(
            "/github/setup-callback",
            cookies={"gh_install_return_to": "/somewhere/else"},
            follow_redirects=False,
        )
        assert res.status_code == 307
        assert res.headers["location"] == "http://testfrontend/integrations/github"


class TestInstallations:
    def test_lists_only_caller_org_installations(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        app = FastAPI()
        app.include_router(github.router)
        from draftly.app.api.auth import get_verified_token

        app.dependency_overrides[get_verified_token] = lambda: {
            "sub": "tester",
            "org_id": "org_2abc",
        }
        state = MagicMock()
        repos = state.dependencies.repositories
        repos.github_installations.list_by_org = AsyncMock(
            return_value=[{"installation_id": 42, "github_org": "acme"}]
        )
        app.state.draftly = state
        client = TestClient(app)

        res = client.get("/github/installations")

        assert res.status_code == 200
        assert res.json() == [{"installation_id": 42, "github_org": "acme"}]
        repos.github_installations.list_by_org.assert_awaited_once_with("org_2abc")
