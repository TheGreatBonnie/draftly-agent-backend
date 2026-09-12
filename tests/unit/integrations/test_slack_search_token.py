"""search.messages resolves the workspace installation bot token."""

import pytest

from draftly.integrations.slack.client import SlackClient


class _FakeInstallation:
    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token


class _FakeStore:
    def __init__(self) -> None:
        self._by_team: dict[str, _FakeInstallation] = {}
        self._by_org: dict[str, _FakeInstallation] = {}

    async def async_get_by_team(self, team_id: str):
        return self._by_team.get(team_id)

    async def async_get_by_org(self, org_id: str):
        return self._by_org.get(org_id)


@pytest.mark.asyncio
async def test_search_uses_team_installation_token(monkeypatch):
    store = _FakeStore()
    store._by_team["T0123"] = _FakeInstallation("xoxb-team-bot")
    client = SlackClient(installation_store=store)

    captured: dict[str, str] = {}

    async def fake_request(method, endpoint, *, params=None, json=None):
        captured["endpoint"] = endpoint
        captured["auth"] = client._headers()["Authorization"]
        return {"ok": True, "messages": {"matches": []}}

    monkeypatch.setattr(client, "_request", fake_request)
    result = await client.search_messages("oauth", team_id="T0123")
    assert result == []
    assert captured["endpoint"] == "search.messages"
    assert captured["auth"] == "Bearer xoxb-team-bot"


@pytest.mark.asyncio
async def test_search_uses_org_installation_token(monkeypatch):
    store = _FakeStore()
    store._by_org["org-1"] = _FakeInstallation("xoxb-org-bot")
    client = SlackClient(installation_store=store)

    captured: dict[str, str] = {}

    async def fake_request(method, endpoint, *, params=None, json=None):
        captured["auth"] = client._headers()["Authorization"]
        return {"ok": True, "messages": {"matches": []}}

    monkeypatch.setattr(client, "_request", fake_request)
    await client.search_messages("oauth", org_id="org-1")
    assert captured["auth"] == "Bearer xoxb-org-bot"


@pytest.mark.asyncio
async def test_search_falls_back_to_resolved_token_when_no_team_or_org(monkeypatch):
    client = SlackClient()
    client.last_token = "xoxb-env-token"

    captured: dict[str, str] = {}

    async def fake_request(method, endpoint, *, params=None, json=None):
        captured["auth"] = client._headers()["Authorization"]
        return {"ok": True, "messages": {"matches": []}}

    monkeypatch.setattr(client, "_request", fake_request)
    result = await client.search_messages("oauth")
    assert result == []
    assert captured["auth"] == "Bearer xoxb-env-token"
