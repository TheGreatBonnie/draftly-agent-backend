"""PushProcessor: GitHub push webhook → normalized push event."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class PushProcessor(BaseProcessor):
    """Normalize ``push`` webhooks (branch pushes trigger doc review)."""

    event_type = EventType.GITHUB_PUSH.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        repo = (payload.get("repository") or {}).get("full_name", "")
        sender = (payload.get("pusher") or {}).get("name", "")
        ref = str(payload.get("ref") or "")
        branch = ref.removeprefix("refs/heads/")

        return ProcessedEvent(
            event_id=event_id or self._derive_id(payload),
            event_type=f"{self.event_type}.pushed",
            repository=repo,
            actor=sender,
            source="github",
            push={
                "ref": ref,
                "branch": branch,
                "before": payload.get("before", ""),
                "after": payload.get("after", ""),
                "default_branch": bool(
                    payload.get("repository", {}).get("default_branch") == branch
                ),
                "commits": [
                    {
                        "id": c.get("id", ""),
                        "message": c.get("message", ""),
                    }
                    for c in payload.get("commits") or []
                ],
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return "ref" in payload and "commits" in payload

    @staticmethod
    def _derive_id(payload: dict[str, Any]) -> str:
        delivery = payload.get("delivery_id")
        if delivery:
            return str(delivery)
        after = str(payload.get("after") or "unknown")
        repo = payload.get("repository", {}).get("full_name", "unknown")
        return f"push-{repo}-{after}"
