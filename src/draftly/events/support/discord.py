"""DiscordProcessor: Discord gateway message → normalized support event."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class DiscordProcessor(BaseProcessor):
    """Normalize Discord message-create payloads into support events.

    Output shape matches the support graph task contract:
    ``{"event_id", "event_type": "discord.message", "source": "discord",
    "source_message_id", "question", ...}``.
    """

    event_type = EventType.DISCORD_SUPPORT.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        guild_id = str(payload.get("guild_id") or "")
        channel_id = str(payload.get("channel_id") or "")
        message_id = str(payload.get("id") or "")
        author = payload.get("author") or {}
        # Reference present ⇒ reply inside a thread.
        referenced = (payload.get("referenced_message") or {}).get("id")

        return ProcessedEvent(
            event_id=event_id or f"discord-{channel_id}-{message_id}",
            event_type=f"{self.event_type}.message",
            repository=None,
            actor=author.get("username", ""),
            source="discord",
            project_id=guild_id,
            source_message_id=f"{channel_id}:{message_id}",
            question=payload.get("content", ""),
            channel=channel_id,
            thread_ts=str(referenced or message_id),
            is_thread_reply=bool(referenced),
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return (
            payload.get("type") in (None, 0)  # 0 = MESSAGE_CREATE
            and bool(payload.get("content"))
            and not (payload.get("author") or {}).get("bot", False)
        )
