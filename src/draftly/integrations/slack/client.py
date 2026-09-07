from __future__ import annotations

from typing import Any, cast

import httpx

from .auth import SlackAuth


class SlackClient:
    BASE_URL = "https://slack.com/api"

    def __init__(
        self,
        auth: SlackAuth | None = None,
        *,
        installation_store: Any | None = None,
        timeout: float = 30.0,
    ):
        # Lazy auth: resolves to `auth`, an event-scoped per-team token, or the
        # SLACK_BOT_TOKEN env fallback at request time.
        self.auth = auth
        self.installation_store = installation_store
        self.timeout = timeout
        self.last_token: str | None = None

    def _resolved_token(self) -> str:
        if self.last_token:
            return self.last_token
        if self.auth is not None and getattr(self.auth, "token", None):
            return self.auth.token
        return SlackAuth().token  # raises when SLACK_BOT_TOKEN is not configured

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolved_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def _resolve_installation_token(self, team_id: str | None) -> None:
        """Resolve the bot token for a team from the installation store.

        ``SLACK_BOT_TOKEN`` remains only an explicit single-tenant fallback;
        event-scoped delivery always prefers the workspace's own installation.
        """
        if not team_id:
            self.last_token = None
            return
        store = self.installation_store
        if store is None:
            from draftly.integrations.support.runtime import (
                default_slack_installation_store,
            )

            store = default_slack_installation_store()
        installation = await store.async_get_by_team(team_id)
        if installation is None:
            raise RuntimeError(f"No Slack installation found for team {team_id}")
        token = getattr(installation, "bot_token", None)
        if not token:
            raise RuntimeError(f"Slack installation for team {team_id} has no bot token")
        self.last_token = token

    async def _resolve_installation_for_org(self, org_id: str | None) -> None:
        """Resolve the bot token for a linked organization's workspace."""
        if not org_id:
            self.last_token = None
            return
        store = self.installation_store
        if store is None or not hasattr(store, "async_get_by_org"):
            from draftly.integrations.support.runtime import (
                default_slack_installation_store,
            )

            store = default_slack_installation_store()
        installation = await store.async_get_by_org(org_id)
        if installation is None:
            raise RuntimeError(f"No Slack installation found for org {org_id}")
        token = getattr(installation, "bot_token", None)
        if not token:
            raise RuntimeError(f"Slack installation for org {org_id} has no bot token")
        self.last_token = token

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                f"{self.BASE_URL}/{endpoint}",
                headers=self._headers(),
                params=params,
                json=json,
            )

        response.raise_for_status()

        data = cast(dict[str, Any], response.json())

        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error', 'unknown error')}")

        return data

    async def search_messages(
        self,
        query: str,
        *,
        channel_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:

        search_query = query

        if channel_id:
            search_query = f"{query} in:{channel_id}"

        data = await self._request(
            "GET",
            "search.messages",
            params={
                "query": search_query,
                "count": limit,
            },
        )

        return cast(
            list[dict[str, Any]],
            data.get("messages", {}).get("matches", []),
        )

    async def send_message(
        self,
        channel_id: str,
        message: str,
        *,
        thread_id: str | None = None,
        team_id: str | None = None,
    ) -> dict[str, Any]:

        await self._resolve_installation_token(team_id)

        payload = {
            "channel": channel_id,
            "text": message,
        }

        if thread_id:
            payload["thread_ts"] = thread_id

        data = await self._request(
            "POST",
            "chat.postMessage",
            json=payload,
        )

        message_data = data.copy()

        message_data.update(
            {
                "channel_id": channel_id,
                "text": message,
                "ts": data.get("ts"),
                "thread_ts": thread_id,
            }
        )

        return message_data

    async def send_dm(
        self,
        user_id: str,
        text: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        org_id: str | None = None,
        team_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a direct message to a user within a resolved organization.

        Resolves the linked workspace installation for ``org_id`` (or
        ``team_id``) before opening the DM so delivery stays within the
        organization's own Slack workspace.
        """
        if org_id:
            await self._resolve_installation_for_org(org_id)
        elif team_id:
            await self._resolve_installation_token(team_id)
        # First open DM channel
        dm_channel = await self._request(
            "POST",
            "conversations.open",
            json={"users": user_id},
        )
        channel_id = dm_channel.get("channel", {}).get("id")
        if not channel_id:
            raise RuntimeError(f"Failed to open DM with user {user_id}")

        payload = {
            "channel": channel_id,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks

        return await self._request("POST", "chat.postMessage", json=payload)

    async def get_conversation_thread(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "conversations.replies",
            params={
                "channel": channel_id,
                "ts": thread_ts,
                "limit": limit,
            },
        )

        return cast(
            list[dict[str, Any]],
            data.get("messages", []),
        )

    async def add_reaction(
        self,
        channel_id: str,
        timestamp: str,
        emoji: str,
    ) -> dict[str, Any]:
        """Add a reaction to a message."""
        return await self._request(
            "POST",
            "reactions.add",
            json={
                "channel": channel_id,
                "timestamp": timestamp,
                "name": emoji,
            },
        )
