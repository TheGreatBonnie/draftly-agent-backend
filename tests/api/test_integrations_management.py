"""Organization-scoped integration management contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from draftly.app.api.routes import integrations
from draftly.app.api.routes.integrations import _connection, _require_org, _require_owned


def test_connection_never_infers_healthy_from_installation_presence():
    item = _connection(
        "github",
        {
            "id": "record",
            "installation_id": 45,
            "github_org": "acme",
            "repositories": [{"id": 1, "full_name": "acme/docs"}],
        },
        None,
    )
    assert item["health"]["status"] == "unknown"
    assert item["sources"] == [
        {"external_id": "1", "name": "acme/docs", "type": "repository", "enabled": True}
    ]


def test_rejects_missing_org_and_other_org_connection():
    with pytest.raises(HTTPException) as missing:
        _require_org({"org_id": ""})
    assert missing.value.status_code == 400
    with pytest.raises(HTTPException) as other:
        _require_owned([{"id": "mine"}], "other")
    assert other.value.status_code == 404


def test_discord_empty_channel_selection_represents_all_channels():
    item = _connection(
        "discord",
        {
            "id": "org_1",
            "discord_guild_id": "guild_1",
            "discord_trigger_channels": [],
            "channels": [{"id": "ch_1", "name": "general"}],
        },
        None,
    )
    assert item["sources"][0]["enabled"] is True


@pytest.mark.asyncio
async def test_rejected_slack_token_records_error_and_emits_change(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": False})

    monkeypatch.setattr(integrations.httpx, "AsyncClient", lambda **kwargs: Client())
    db = SimpleNamespace()
    db.fetch_all = AsyncMock(return_value=[{"id": "slack_1", "team_id": "T1", "team_name": "Acme"}])
    saved = {"status": "unknown"}

    async def fetch_one(query, *args):
        if "bot_token" in query: return {"bot_token": "secret"}
        if "integration_health" in query: return {"status": saved["status"], "message": None, "checked_at": None}
        return None

    async def execute(query, *args):
        saved["status"] = args[3]

    db.fetch_one = fetch_one
    db.execute = execute
    bus = SimpleNamespace(broadcast=AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        draftly=SimpleNamespace(dependencies=SimpleNamespace(integrations=SimpleNamespace(database=db))),
        dashboard_broadcaster=bus,
    )))
    result = await integrations.refresh_integration("slack", "slack_1", request, {"org_id": "org_1"})
    assert result["health"]["status"] == "error"
    bus.broadcast.assert_awaited_once_with("org_1", "integration:changed", {"provider": "slack"})
