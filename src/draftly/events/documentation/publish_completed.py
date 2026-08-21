"""PublishCompletedProcessor: documentation publish completion events."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class PublishCompletedProcessor(BaseProcessor):
    """Normalize publish-completion events emitted after doc delivery."""

    event_type = EventType.DOCUMENTATION_PUBLISHED.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        receipt = payload.get("receipt") or {}

        return ProcessedEvent(
            event_id=event_id or f"doc-published-{payload.get('run_id', 'unknown')}",
            event_type=f"{self.event_type}.completed",
            repository=payload.get("repository", ""),
            actor=payload.get("actor", ""),
            source="documentation",
            project_id=payload.get("project_id"),
            run_id=payload.get("run_id"),
            receipt={
                "delivered_to": receipt.get("delivered_to", ""),
                "surface": receipt.get("surface", ""),
                "reference": receipt.get("reference", ""),
                "status": receipt.get("status", ""),
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return "receipt" in payload
