from __future__ import annotations

from typing import Any, cast

import httpx

from .auth import SlackAuth


class SlackClient:
    BASE_URL = "https://slack.com/api"

    def __init__(
        self,
        auth: SlackAuth | None = None,
        timeout: float = 30.0,
    ):
        self.auth = auth or SlackAuth()
        self.timeout = timeout

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
                headers=self.auth.headers(),
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
    ) -> dict[str, Any]:

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
    ) -> dict[str, Any]:
        """Send a direct message to a user."""
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
