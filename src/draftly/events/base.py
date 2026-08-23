"""Base event processor interface (plan §7.1).

Processors are pure normalizers: raw provider webhook payload in, typed
Draftly event out. No I/O, no graph knowledge — the dispatcher and runner
own routing and execution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class ProcessedEvent(BaseModel):
    """Normalized event as graphs and workflows consume it.

    Shape contract (must match Phase 4 graph task JSON):
    ``event_id``, ``event_type`` ("<prefix>.<action>"), ``repository``,
    ``actor``, plus a type-specific body (``pull_request`` / ``issue`` /
    ``question`` / ...).
    """

    model_config = {"extra": "allow"}

    event_id: str
    event_type: str
    repository: str | None = None
    actor: str | None = None
    project_id: str | None = None
    source: str | None = None


class BaseProcessor(ABC):
    """Normalize one provider's webhooks into :class:`ProcessedEvent`."""

    #: Canonical EventType prefix this processor handles.
    event_type: str = ""

    @abstractmethod
    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        """Normalize a raw payload."""

    def supports(self, payload: dict[str, Any]) -> bool:
        """Whether this processor recognizes the payload."""
        return True

    def _action(self, payload: dict[str, Any], default: str = "updated") -> str:
        action = str(payload.get("action") or default)
        return action.strip().lower().replace("-", "_") or default
