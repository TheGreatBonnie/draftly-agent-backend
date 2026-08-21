"""Shared memory item base model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class MemoryItem(BaseModel):
    """Base shape shared by every memory record."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    org_id: str | None = None
    namespace: str
    content: str
    memory_type: str = "fact"
    importance: float = 0.5
    confidence: float = 0.5
    metadata: dict[str, Any] = {}
    created_at: datetime | None = None
    updated_at: datetime | None = None
