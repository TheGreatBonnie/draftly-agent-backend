"""§9.2 verification: installation-aware support clients + org-scoped runtime.

Slack delivery resolves the bot token from the current event's team id via
the installation store (`SLACK_BOT_TOKEN` is only a fallback). Discord delivery
validates the guild target before sending. Tools obtain their tenant target from
the active ``SupportRuntimeContext`` and refuse to guess another tenant.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from draftly.integrations.discord.client import DiscordClient
from draftly.integrations.slack.client import SlackClient
from draftly.integrations.support.runtime import (
    SupportRuntimeContext,
    current_support_runtime,
    reset_support_runtime,
    set_support_runtime,
)


class FakeInstallations:
    def __init__(self, tokens):
        self.tokens = tokens
        self.by_org: dict[str, str] = {}

    async def async_get_by_team(self, team_id):
        installation = self.tokens.get(team_id)
        return SimpleNamespace(bot_token=installation) if installation else None

    async def async_get_by_org(self, org_id):
        installation = self.by_org.get(org_id)
        return SimpleNamespace(bot_token=installation) if installation else None


class TestSupportRuntime:
    def test_set_current_reset_round_trip(self) -> None:
        ctx = SupportRuntimeContext(
            org_id="org-1",
            platform="slack",
            platform_account_id="T1",
            channel_id="C1",
            thread_id="1",
        )
        token = set_support_runtime(ctx)
        try:
            assert current_support_runtime() == ctx
        finally:
            reset_support_runtime(token)
        assert current_support_runtime() is None

    async def test_runtime_is_scoped_to_async_task(self) -> None:
        import asyncio

        parent = current_support_runtime()
        token = set_support_runtime(
            SupportRuntimeContext(org_id="org-1", platform="discord")
        )

        async def child() -> None:
            # A contextvar set in the parent is visible; a re-set inside the
            # child must not leak back to the parent.
            pass

        try:
            await asyncio.create_task(child())
            assert current_support_runtime() is not None
            assert current_support_runtime().org_id == "org-1"
        finally:
            reset_support_runtime(token)
        assert current_support_runtime() is None
        assert parent is None


class TestDefaultSlackInstallationStore:
    async def test_build_dependencies_receives_settings(self, monkeypatch) -> None:
        from draftly.integrations.slack.installation_store import SlackInstallationStore
        from draftly.integrations.support.runtime import (
            default_slack_installation_store,
        )

        captured: dict[str, Any] = {}

        def fake_build_dependencies(*, settings):
            captured["settings"] = settings
            return SimpleNamespace(
                integrations=SimpleNamespace(database=SimpleNamespace())
            )

        monkeypatch.setattr(
            "draftly.app.dependencies.build_dependencies", fake_build_dependencies
        )

        store = default_slack_installation_store()

        assert captured["settings"] is not None
        assert isinstance(store, SlackInstallationStore)


class TestSlackClientInstallationAware:
    def make_client(self, tokens=None) -> SlackClient:
        return SlackClient(installation_store=FakeInstallations(tokens or {}))

    async def test_slack_delivery_uses_event_team_installation_token(
        self, monkeypatch
    ) -> None:
        client = self.make_client({"T1": "xoxb-team"})
        captured: dict[str, Any] = {}

        async def fake_request(method: str, endpoint: str, **kwargs):
            captured["headers"] = client._headers()
            captured["payload"] = kwargs.get("json")
            return {"ok": True, "ts": "1.2"}

        monkeypatch.setattr(client, "_request", fake_request)

        await client.send_message("C1", "answer", thread_id="1", team_id="T1")

        assert client.last_token == "xoxb-team"
        assert captured["headers"]["Authorization"] == "Bearer xoxb-team"
        assert captured["payload"]["channel"] == "C1"
        assert captured["payload"]["thread_ts"] == "1"

    async def test_slack_delivery_missing_installation_raises(self) -> None:
        client = self.make_client({"T1": "xoxb-team"})

        with pytest.raises(RuntimeError, match="No Slack installation"):
            await client.send_message("C1", "answer", team_id="T2")


class TestDiscordClientGuildValidation:
    async def test_discord_delivery_rejects_unlinked_guild(self) -> None:
        client = DiscordClient(allowed_guilds={"G1"})

        with pytest.raises(PermissionError):
            await client.send_message("C1", "answer", guild_id="G2")

    async def test_discord_delivery_rejects_missing_guild(self) -> None:
        client = DiscordClient(allowed_guilds={"G1"})

        with pytest.raises(PermissionError):
            await client.send_message("C1", "answer")

    async def test_discord_delivery_allows_configured_guild(self, monkeypatch) -> None:
        client = DiscordClient(allowed_guilds={"G1"})
        captured: dict[str, Any] = {}

        async def fake_request(method: str, path: str, **kwargs):
            captured["path"] = path
            captured["payload"] = kwargs.get("json")
            return {"id": "msg-1"}

        monkeypatch.setattr(client, "_request", fake_request)

        await client.send_message("C1", "answer", guild_id="G1", thread_id="T1")

        assert captured["path"] == "/channels/T1/messages"
        assert captured["payload"]["content"] == "answer"

    async def test_discord_no_allowed_guilds_skips_validation(self, monkeypatch) -> None:
        client = DiscordClient()

        async def fake_request(method: str, path: str, **kwargs):
            return {"id": "msg-1"}

        monkeypatch.setattr(client, "_request", fake_request)
        assert await client.send_message("C1", "answer") == {"id": "msg-1"}


class TestToolsReadRuntimeTarget:
    def _context(self, token, *, platform="slack") -> SupportRuntimeContext:
        token = set_support_runtime(
            SupportRuntimeContext(
                org_id="org-1",
                platform=platform,
                platform_account_id=token,
                channel_id="C1",
                thread_id="9",
            )
        )
        return token

    async def test_slack_tool_without_runtime_raises(self) -> None:
        from draftly.tools.slack.post_message import post_message

        with pytest.raises(RuntimeError, match="support runtime"):
            await post_message("C1", "answer")

    async def test_slack_tool_forwards_runtime_target(self, monkeypatch) -> None:

        calls: dict[str, Any] = {}

        class FakeClient:
            def __init__(self, installation_store=None):
                del installation_store

            async def send_message(self, channel, text, *, thread_id=None, team_id=None):
                calls.update(
                    channel=channel, text=text, thread_id=thread_id, team_id=team_id
                )
                return {"ok": True}

        monkeypatch.setattr("draftly.integrations.slack.client.SlackClient", FakeClient)
        monkeypatch.setattr(
            "draftly.integrations.support.runtime.default_slack_installation_store",
            lambda: "store",
        )

        token = self._context("T1")
        try:
            from draftly.tools.slack.post_message import post_message

            await post_message("C1", "answer")
        finally:
            reset_support_runtime(token)

        assert calls["team_id"] == "T1"
        assert calls["thread_id"] == "9"

    async def test_discord_tool_without_runtime_raises(self) -> None:
        from draftly.tools.discord.post_message import post_message

        with pytest.raises(RuntimeError, match="support runtime"):
            await post_message("C1", "answer")

    async def test_discord_tool_forwards_runtime_target(self, monkeypatch) -> None:
        calls: dict[str, Any] = {}

        class FakeClient:
            def __init__(self, allowed_guilds=None):
                calls["allowed_guilds"] = allowed_guilds

            async def send_message(self, channel_id, content, *, thread_id=None, guild_id=None):
                calls.update(
                    channel_id=channel_id,
                    content=content,
                    thread_id=thread_id,
                    guild_id=guild_id,
                )
                return {"id": "m1"}

        monkeypatch.setattr("draftly.integrations.discord.client.DiscordClient", FakeClient)

        token = self._context("G1", platform="discord")
        try:
            from draftly.tools.discord.post_message import post_message

            await post_message("C1", "answer")
        finally:
            reset_support_runtime(token)

        assert calls["guild_id"] == "G1"
        assert calls["allowed_guilds"] == {"G1"}
        assert calls["thread_id"] == "9"
