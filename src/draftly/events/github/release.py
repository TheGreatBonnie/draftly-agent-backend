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
        content_relevant = action == "published" and not bool(release.get("draft"))

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
                "content_relevant": content_relevant,
                "source_event_type": "release",
                "source_event_id": str(release.get("id") or event_id or ""),
                "source_title": release.get("name") or release.get("tag_name", ""),
                "source_summary": release.get("body") or release.get("name") or "",
                "source_evidence": ([{"source_id": release.get("html_url")}]
                                     if release.get("html_url") else []),
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
