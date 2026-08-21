"""Discord delivery (plan §8.6) — post message, thread reply."""

from __future__ import annotations

from typing import Any

from draftly.integrations.discord.client import DiscordClient


class DiscordDelivery:
    """Deliver answers to Discord channels/threads."""

    def __init__(self, client: DiscordClient | None = None) -> None:
        self.client = client or DiscordClient()

    async def post_message(
        self,
        *,
        channel_id: str,
        message: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.client.send_message(
            channel_id,
            message,
            thread_id=thread_id,
        )

    async def reply_in_thread(
        self,
        *,
        thread_id: str,
        message: str,
    ) -> dict[str, Any]:
        return await self.client.send_thread_message(thread_id, message)

    async def create_thread(
        self,
        *,
        channel_id: str,
        message_id: str,
        name: str,
    ) -> dict[str, Any]:
        return await self.client.create_thread(
            channel_id,
            message_id,
            name,
        )
