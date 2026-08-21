"""DocumentChangedProcessor: internal doc-change events."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class DocumentChangedProcessor(BaseProcessor):
    """Normalize documentation-change events (repo webhooks, sync jobs)."""

    event_type = EventType.DOCUMENTATION_CHANGED.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        document = payload.get("document") or {}
        changed = self._action(payload, default="changed")

        return ProcessedEvent(
            event_id=event_id
            or f"doc-changed-{document.get('id', payload.get('repository', 'unknown'))}",
            event_type=f"{self.event_type}.{changed}",
            repository=payload.get("repository", ""),
            actor=payload.get("actor", ""),
            source="documentation",
            project_id=payload.get("project_id"),
            document={
                "id": document.get("id"),
                "path": document.get("path", ""),
                "title": document.get("title", ""),
                "version": document.get("version"),
                "change": changed,
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return "document" in payload
