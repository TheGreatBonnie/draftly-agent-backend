"""ReleaseProcessor: GitHub release webhook → normalized release event."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class ReleaseProcessor(BaseProcessor):
    """Normalize ``release`` webhooks (published releases trigger doc sync)."""

    event_type = EventType.GITHUB_RELEASE.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        release = payload.get("release") or {}
        repo = (payload.get("repository") or {}).get("full_name", "")
        sender = (payload.get("sender") or {}).get("login", "")
        action = self._action(payload, default="published")

        return ProcessedEvent(
            event_id=event_id or self._derive_id(payload, release, action),
            event_type=f"{self.event_type}.{action}",
            repository=repo,
            actor=sender,
            source="github",
            release={
                "id": release.get("id"),
                "tag_name": release.get("tag_name", ""),
                "name": release.get("name", ""),
                "draft": bool(release.get("draft", False)),
                "prerelease": bool(release.get("prerelease", False)),
                "html_url": release.get("html_url", ""),
                "action": action,
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("release"))

    @staticmethod
    def _derive_id(payload: dict[str, Any], release: dict[str, Any], action: str) -> str:
        delivery = payload.get("delivery_id")
        if delivery:
            return str(delivery)
        tag = release.get("tag_name", "unknown")
        return f"release-{payload.get('repository', {}).get('full_name', 'unknown')}-{tag}-{action}"
