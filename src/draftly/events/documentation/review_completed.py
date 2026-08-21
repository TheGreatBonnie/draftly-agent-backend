"""ReviewCompletedProcessor: human review decision events."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class ReviewCompletedProcessor(BaseProcessor):
    """Normalize review-completion events (approve/reject from §9 route)."""

    event_type = EventType.REVIEW_COMPLETED.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        decision = payload.get("decision") or self._action(payload, default="approved")

        return ProcessedEvent(
            event_id=event_id or f"review-{payload.get('run_id', 'unknown')}-{decision}",
            event_type=f"{self.event_type}.{decision}",
            repository=payload.get("repository", ""),
            actor=payload.get("reviewer_id", ""),
            source="documentation",
            project_id=payload.get("project_id"),
            run_id=payload.get("run_id"),
            review={
                "decision": decision,
                "comment": payload.get("comment", ""),
                "interrupt_id": payload.get("interrupt_id"),
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return "decision" in payload or "run_id" in payload
