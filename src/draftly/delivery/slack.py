"""Slack delivery (plan §8.6) — post message, thread reply."""

from __future__ import annotations

from typing import Any

from draftly.integrations.slack.client import SlackClient


class SlackDelivery:
    """Deliver answers to Slack channels/threads."""

    def __init__(self, client: SlackClient | None = None) -> None:
        self.client = client or SlackClient()

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
        channel_id: str,
        thread_ts: str,
        message: str,
    ) -> dict[str, Any]:
        return await self.client.send_message(
            channel_id,
            message,
            thread_id=thread_ts,
        )

    async def add_reaction(
        self,
        *,
        channel_id: str,
        timestamp: str,
        reaction: str = "white_check_mark",
    ) -> dict[str, Any]:
        return await self.client.add_reaction(
            channel_id=channel_id,
            timestamp=timestamp,
            emoji=reaction,
        )
