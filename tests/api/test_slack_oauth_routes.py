"""Slack OAuth routes preserve a safe caller return path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes import slack


@pytest.fixture()
def app() -> FastAPI:
    api = FastAPI()
    api.include_router(slack.router)
    api.dependency_overrides[get_verified_token] = lambda: {
        "user_id": "user_1",
        "org_id": "org_1",
        "org_role": "admin",
    }
    db = MagicMock()
    db.execute = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)
    api.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            integrations=SimpleNamespace(database=db),
        )
    )
    return api


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
    monkeypatch.setattr(slack.settings, "slack_client_id", "slack-client")
    monkeypatch.setattr(slack.settings, "slack_client_secret", "slack-secret")
    monkeypatch.setattr(slack.settings, "slack_scopes", None)
    monkeypatch.setattr(
        slack.settings,
        "slack_redirect_uri",
        "https://api.example.com/api/slack/oauth/callback",
    )
    monkeypatch.setattr(slack.settings, "frontend_url", "https://app.example.com")


def test_install_url_stores_onboarding_return_path(app, monkeypatch):
    create_state = AsyncMock(return_value="nonce_1")
    monkeypatch.setattr(slack, "create_state", create_state)

    with TestClient(app) as client:
        response = client.get(
            "/slack/install-url?return_to=/onboarding/integrations"
        )

    assert response.status_code == 200
    assert create_state.await_args.args[1:] == (
        "slack",
        "org_1",
        "user_1",
        "/onboarding/integrations",
    )
    assert "state=nonce_1" in response.json()["install_url"]


def test_install_url_requests_every_scope_used_by_runtime_handlers(app, monkeypatch):
    monkeypatch.setattr(slack, "create_state", AsyncMock(return_value="nonce_1"))

    with TestClient(app) as client:
        response = client.get("/slack/install-url")

    query = parse_qs(urlparse(response.json()["install_url"]).query)
    assert set(query["scope"][0].split(",")) == {
        "app_mentions:read",
        "channels:history",
        "channels:read",
        "chat:write",
        "groups:history",
        "groups:read",
        "im:history",
        "mpim:history",
        "reactions:write",
    }


def test_install_url_rejects_external_return_path(app):
    with TestClient(app) as client:
        response = client.get(
            "/slack/install-url?return_to=https://evil.example"
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid return_to path"}


def test_callback_redirects_to_consumed_return_path(app, monkeypatch):
    monkeypatch.setattr(
        slack,
        "consume_state",
        AsyncMock(
            return_value={
                "org_id": "org_1",
                "user_id": "user_1",
                "return_to": "/onboarding/integrations",
            }
        ),
    )

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "ok": True,
        "team": {"id": "T1", "name": "Acme"},
        "authed_user": {"id": "U1"},
        "bot_user_id": "B1",
        "access_token": "xoxb-test",
        "scope": "chat:write",
    }
    http = AsyncMock()
    http.__aenter__.return_value.post = AsyncMock(return_value=response)
    monkeypatch.setattr(slack.httpx, "AsyncClient", lambda: http)

    store = MagicMock()
    store.async_save = AsyncMock()
    monkeypatch.setattr(slack, "SlackInstallationStore", lambda db: store)

    with TestClient(app) as client:
        result = client.get(
            "/slack/oauth/callback?code=code_1&state=state_1",
            follow_redirects=False,
        )

    assert result.status_code == 307
    assert result.headers["location"] == (
        "https://app.example.com/onboarding/integrations"
        "?oauth_provider=slack&oauth_status=connected"
    )
    assert store.async_save.await_args.kwargs["org_id"] == "org_1"


def test_callback_returns_to_onboarding_when_user_cancels(app, monkeypatch):
    monkeypatch.setattr(
        slack,
        "consume_state",
        AsyncMock(
            return_value={
                "org_id": "org_1",
                "user_id": "user_1",
                "return_to": "/onboarding/integrations",
            }
        ),
    )

    with TestClient(app) as client:
        response = client.get(
            "/slack/oauth/callback?state=state_1&error=access_denied",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://app.example.com/onboarding/integrations"
        "?oauth_provider=slack&oauth_status=cancelled"
    )


def test_callback_returns_sanitized_failure_when_exchange_fails(app, monkeypatch):
    monkeypatch.setattr(
        slack,
        "consume_state",
        AsyncMock(
            return_value={
                "org_id": "org_1",
                "user_id": "user_1",
                "return_to": "/onboarding/integrations",
            }
        ),
    )
    http = AsyncMock()
    http.__aenter__.return_value.post = AsyncMock(
        side_effect=httpx.ConnectError("provider unavailable")
    )
    monkeypatch.setattr(slack.httpx, "AsyncClient", lambda: http)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/slack/oauth/callback?code=secret-code&state=state_1",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://app.example.com/onboarding/integrations"
        "?oauth_provider=slack&oauth_status=failed"
    )
    assert "secret-code" not in response.headers["location"]
