"""SlackProcessor: Slack event callback → normalized support event."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType

# Slack message subtypes we ignore (edits, joins, bots...).
IGNORED_SUBTYPES = {
    "message_changed",
    "message_deleted",
    "channel_join",
    "channel_leave",
    "bot_message",
}


class SlackProcessor(BaseProcessor):
    """Normalize Slack ``event_callback`` payloads into support events.

    Output shape matches the support graph task contract:
    ``{"event_id", "event_type": "slack.message", "source": "slack",
    "source_message_id", "question", ...}``.
    """

    event_type = EventType.SLACK_SUPPORT.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        message = payload.get("event") or {}
        team_id = payload.get("team_id", "")
        channel = message.get("channel", "")
        ts = str(message.get("ts", ""))
        thread_ts = str(message.get("thread_ts") or ts)

        return ProcessedEvent(
            event_id=event_id or f"slack-{team_id}-{channel}-{ts}",
            event_type=f"{self.event_type}.message",
            repository=None,
            actor=message.get("user", ""),
            source="slack",
            project_id=team_id,
            team_id=team_id,
            source_message_id=f"{channel}:{ts}",
            question=message.get("text", ""),
            channel=channel,
            thread_ts=thread_ts,
            is_thread_reply=bool(message.get("thread_ts")),
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        event = payload.get("event") or {}
        return (
            event.get("type") == "message"
            and event.get("subtype") not in IGNORED_SUBTYPES
            and bool(event.get("text"))
        )
