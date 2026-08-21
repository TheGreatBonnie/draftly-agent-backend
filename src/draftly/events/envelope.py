"""Event envelope: raw payload + routing metadata (plan §7.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from draftly.events.types import EventType


class EventEnvelope(BaseModel):
    """Wraps a raw webhook payload with the metadata the pipeline needs.

    ``payload`` is the normalized event body produced by a processor;
    ``metadata`` carries transport-level facts (delivery id, project,
    timestamps) that must never leak into graph prompts.
    """

    model_config = ConfigDict(extra="allow")

    event_type: EventType | str
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def event_id(self) -> str:
        return str(self.metadata.get("event_id") or self.payload.get("event_id") or uuid4())

    @property
    def source(self) -> str:
        et = (
            self.event_type.value
            if isinstance(self.event_type, EventType)
            else str(self.event_type)
        )
        return et.split(".")[0]

    def stamp(self, **meta: Any) -> EventEnvelope:
        """Return a copy with additional metadata (immutability-friendly)."""
        merged = {**self.metadata, **meta}
        return self.model_copy(update={"metadata": merged})

    def to_task(self) -> dict[str, Any]:
        """Merge payload + identity into the task dict graphs consume."""
        task = {
            **self.payload,
            "event_id": self.event_id,
            "event_type": (
                self.event_type.value
                if isinstance(self.event_type, EventType)
                else str(self.event_type)
            ),
        }
        for key in ("project_id", "source", "repository", "actor"):
            if key in self.metadata and key not in task:
                task[key] = self.metadata[key]
        return task


def envelope_for(
    event_type: EventType | str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    **metadata: Any,
) -> EventEnvelope:
    """Convenience constructor stamping defaults."""
    return EventEnvelope(
        event_type=event_type,
        payload=payload,
        metadata={
            "event_id": event_id or str(payload.get("event_id") or uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
            **metadata,
        },
    )
