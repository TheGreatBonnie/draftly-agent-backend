"""Support domain events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SupportEvent(BaseModel):
    """A normalized support-related domain event."""

    model_config = ConfigDict(extra="allow")

    event_id: str
    platform: str
    event_type: str
    occurred_at: datetime | None = None
    actor_id: str | None = None
    payload: dict[str, Any] = {}
