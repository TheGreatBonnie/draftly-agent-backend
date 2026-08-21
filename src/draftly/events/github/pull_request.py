"""PullRequestProcessor: GitHub PR webhook → normalized PR event."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class PullRequestProcessor(BaseProcessor):
    """Normalize ``pull_request`` webhooks into graph-ready events.

    Output shape matches the documentation graph task contract:
    ``{"event_id", "event_type": "pull_request.<action>", "repository",
    "actor", "pull_request": {"number", "title", ...}}``.
    """

    event_type = EventType.GITHUB_PULL_REQUEST.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name", "")
        sender = (payload.get("sender") or {}).get("login", "")
        action = self._action(payload, default="updated")

        return ProcessedEvent(
            event_id=event_id or self._derive_id(payload, pr, action),
            event_type=f"{self.event_type}.{action}",
            repository=repo,
            actor=sender,
            source="github",
            pull_request={
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "state": pr.get("state", ""),
                "sha": (pr.get("head") or {}).get("sha", ""),
                "base": (pr.get("base") or {}).get("ref", ""),
                "html_url": pr.get("html_url", ""),
                "action": action,
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("pull_request"))

    @staticmethod
    def _derive_id(payload: dict[str, Any], pr: dict[str, Any], action: str) -> str:
        delivery = payload.get("delivery_id")
        if delivery:
            return str(delivery)
        number = pr.get("number", "unknown")
        return f"pr-{payload.get('repository', {}).get('full_name', 'unknown')}-{number}-{action}"
