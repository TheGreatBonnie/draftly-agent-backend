"""Support surface task state."""

from __future__ import annotations

from pydantic import BaseModel


class SupportEvent(BaseModel):
    """Normalized support surface event (the graph's task JSON)."""

    event_id: str
    event_type: str  # "slack.message" | "discord.message"
    project_id: str
    source: str  # "slack" | "discord"
    source_message_id: str
    repository: str | None = None
    question: str
