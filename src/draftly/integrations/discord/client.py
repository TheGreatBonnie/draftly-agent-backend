from __future__ import annotations

from typing import Any, cast

import httpx

from .auth import DiscordAuth


class DiscordClient:

    BASE_URL = (
        "https://discord.com/api/v10"
    )

    def __init__(
        self,
        auth: DiscordAuth | None = None,
        timeout: float = 30.0,
    ):
        self.auth = auth or DiscordAuth()
        self.timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:

        async with httpx.AsyncClient(
            timeout=self.timeout
        ) as client:

            response = await client.request(
                method,
                f"{self.BASE_URL}{path}",
                headers=self.auth.headers(),
                params=params,
                json=json,
            )

        response.raise_for_status()

        return response.json()

    async def search_messages(
        self,
        query: str,
        *,
        channel_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:

        if not channel_id:
            raise ValueError(
                "Discord message search requires "
                "a channel_id in this example."
            )

        messages = await self._request(
            "GET",
            f"/channels/{channel_id}/messages",
            params={
                "limit": 100,
            },
        )

        query_lower = query.lower()

        matches = [
            message
            for message in messages
            if query_lower
            in message.get(
                "content",
                "",
            ).lower()
        ]

        return matches[:limit]

    async def send_message(
        self,
        channel_id: str,
        message: str,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:

        target_channel = (
            thread_id or channel_id
        )

        return cast(dict[str, Any], await self._request(
            "POST",
            f"/channels/{target_channel}/messages",
            json={
                "content": message,
            },
        ))

    async def create_thread(
        self,
        channel_id: str,
        message_id: str,
        name: str,
    ) -> dict[str, Any]:
        """Create a public thread from an existing message."""
        return cast(dict[str, Any], await self._request(
            "POST",
            f"/channels/{channel_id}/messages/{message_id}/threads",
            json={
                "name": name,
                "auto_archive_duration": 60,
            },
        ))

    async def send_thread_message(
        self,
        thread_id: str,
        content: str,
        *,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a message to a Discord thread."""
        payload: dict[str, Any] = {"content": content}
        if embeds:
            payload["embeds"] = embeds
        if components:
            payload["components"] = components
        return cast(dict[str, Any], await self._request(
            "POST",
            f"/channels/{thread_id}/messages",
            json=payload,
        ))

    async def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> dict[str, Any]:
        """Add a reaction to a message."""
        import urllib.parse

        encoded_emoji = urllib.parse.quote(emoji)
        return cast(dict[str, Any], await self._request(
            "PUT",
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me",
        ))

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message."""
        payload: dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = components
        return cast(dict[str, Any], await self._request(
            "PATCH",
            f"/channels/{channel_id}/messages/{message_id}",
            json=payload,
        ))
