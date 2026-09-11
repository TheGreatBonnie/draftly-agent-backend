from __future__ import annotations

from draftly.integrations.discord.client import DiscordClient


async def test_send_dm_forwards_embeds_and_components(monkeypatch) -> None:
    client = DiscordClient()
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        calls.append((method, path, json))
        if path == "/guilds/g1/members/U1":
            return {}
        if path == "/users/@me/channels":
            return {"id": "dm-1"}
        if path == "/channels/dm-1/messages":
            return {"id": "msg-1"}
        return {}

    monkeypatch.setattr(client, "_request", fake_request)

    await client.send_dm(
        "U1",
        "summary\nReview: review-1",
        guild_id="g1",
        embeds=[{"title": "Documentation Review Required"}],
        components=[{"type": 1, "components": [{"type": 2, "custom_id": "approve"}]}],
    )

    post = next(call for call in calls if call[1] == "/channels/dm-1/messages")
    assert post[2] == {
        "content": "summary\nReview: review-1",
        "embeds": [{"title": "Documentation Review Required"}],
        "components": [{"type": 1, "components": [{"type": 2, "custom_id": "approve"}]}],
    }


async def test_send_dm_omits_empty_content(monkeypatch) -> None:
    client = DiscordClient()
    posts: list[dict | None] = []

    async def fake_request(
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        if path == "/guilds/g1/members/U1":
            return {}
        if path == "/users/@me/channels":
            return {"id": "dm-1"}
        if path == "/channels/dm-1/messages":
            posts.append(json)
            return {}
        return {}

    monkeypatch.setattr(client, "_request", fake_request)

    await client.send_dm(
        "U1",
        "",
        guild_id="g1",
        embeds=[{"title": "Documentation Review Required"}],
    )

    assert posts == [{"embeds": [{"title": "Documentation Review Required"}]}]


async def test_send_dm_plain_when_no_embeds(monkeypatch) -> None:
    client = DiscordClient()
    posts: list[dict | None] = []

    async def fake_request(
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        if path == "/guilds/g1/members/U1":
            return {}
        if path == "/users/@me/channels":
            return {"id": "dm-1"}
        if path == "/channels/dm-1/messages":
            posts.append(json)
            return {}
        return {}

    monkeypatch.setattr(client, "_request", fake_request)

    await client.send_dm("U1", "hello", guild_id="g1")

    assert posts == [{"content": "hello"}]
