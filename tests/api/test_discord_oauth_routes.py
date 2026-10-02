"""Discord OAuth routes preserve a safe caller return path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes import discord


@pytest.fixture()
def app() -> FastAPI:
    api = FastAPI()
    api.include_router(discord.router)
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
    monkeypatch.setattr(discord.settings, "discord_app_id", "discord-app")
    monkeypatch.setattr(discord.settings, "discord_client_secret", "discord-secret")
    monkeypatch.setattr(discord.settings, "discord_bot_token", "discord-bot")
    monkeypatch.setattr(discord.settings, "public_api_url", "https://api.example.com")
    monkeypatch.setattr(discord.settings, "frontend_url", "https://app.example.com")


def test_invite_url_stores_onboarding_return_path(app, monkeypatch):
    create_state = AsyncMock(return_value="nonce_1")
    monkeypatch.setattr(discord, "create_state", create_state)

    with TestClient(app) as client:
        response = client.get(
            "/discord/invite-url?return_to=/onboarding/integrations"
        )

    assert response.status_code == 200
    assert create_state.await_args.args[1:] == (
        "discord",
        "org_1",
        "user_1",
        "/onboarding/integrations",
    )
    assert "state=nonce_1" in response.json()["invite_url"]


def test_invite_url_requests_permissions_used_by_runtime_handlers(app, monkeypatch):
    monkeypatch.setattr(discord, "create_state", AsyncMock(return_value="nonce_1"))

    with TestClient(app) as client:
        response = client.get("/discord/invite-url")

    query = parse_qs(urlparse(response.json()["invite_url"]).query)
    permissions = int(query["permissions"][0])
    expected = (
        (1 << 6)  # ADD_REACTIONS
        | (1 << 10)  # VIEW_CHANNEL
        | (1 << 11)  # SEND_MESSAGES
        | (1 << 14)  # EMBED_LINKS
        | (1 << 15)  # ATTACH_FILES
        | (1 << 16)  # READ_MESSAGE_HISTORY
        | (1 << 35)  # CREATE_PUBLIC_THREADS
        | (1 << 38)  # SEND_MESSAGES_IN_THREADS
    )
    assert permissions == expected


def test_invite_url_rejects_external_return_path(app):
    with TestClient(app) as client:
        response = client.get(
            "/discord/invite-url?return_to=https://evil.example"
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid return_to path"}


def test_callback_consumes_state_and_redirects_to_its_return_path(app, monkeypatch):
    consume_state = AsyncMock(
        return_value={
            "org_id": "org_1",
            "user_id": "user_1",
            "return_to": "/onboarding/integrations",
        }
    )
    monkeypatch.setattr(discord, "consume_state", consume_state)

    token_response = MagicMock()
    token_response.raise_for_status.return_value = None
    token_response.json.return_value = {"access_token": "oauth-token"}
    guilds_response = MagicMock()
    guilds_response.raise_for_status.return_value = None
    guilds_response.json.return_value = [{"id": "G1", "permissions": 8}]
    bot_response = MagicMock(status_code=200)

    client = AsyncMock()
    client.post = AsyncMock(return_value=token_response)
    client.get = AsyncMock(side_effect=[guilds_response, bot_response])
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(discord.httpx, "AsyncClient", lambda **kwargs: context)

    db = app.state.draftly.dependencies.integrations.database
    db.fetch_one.return_value = None

    with TestClient(app) as test_client:
        response = test_client.get(
            "/discord/oauth/callback?code=code_1&state=state_1&guild_id=G1",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://app.example.com/onboarding/integrations"
        "?oauth_provider=discord&oauth_status=connected"
    )
    consume_state.assert_awaited_once_with(db, "discord", "state_1")
    assert db.execute.await_args.args[1:] == ("G1", "org_1")


def test_callback_returns_to_onboarding_when_user_cancels(app, monkeypatch):
    monkeypatch.setattr(
        discord,
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
            "/discord/oauth/callback?state=state_1&error=access_denied",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://app.example.com/onboarding/integrations"
        "?oauth_provider=discord&oauth_status=cancelled"
    )


def test_callback_returns_sanitized_failure_when_exchange_fails(app, monkeypatch):
    monkeypatch.setattr(
        discord,
        "consume_state",
        AsyncMock(
            return_value={
                "org_id": "org_1",
                "user_id": "user_1",
                "return_to": "/onboarding/integrations",
            }
        ),
    )
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("provider unavailable"))
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(discord.httpx, "AsyncClient", lambda **kwargs: context)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/discord/oauth/callback?code=secret-code&state=state_1&guild_id=G1",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://app.example.com/onboarding/integrations"
        "?oauth_provider=discord&oauth_status=failed"
    )
    assert "secret-code" not in response.headers["location"]
