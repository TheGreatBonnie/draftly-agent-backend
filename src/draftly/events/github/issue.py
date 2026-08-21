"""IssueProcessor: GitHub issue webhook → normalized issue event."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType


class IssueProcessor(BaseProcessor):
    """Normalize ``issues`` webhooks into graph-ready events.

    Output shape: ``{"event_id", "event_type": "issues.<action>",
    "repository", "actor", "issue": {"number", "title", ...}}``.
    """

    event_type = EventType.GITHUB_ISSUE.value

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        issue = payload.get("issue") or {}
        repo = (payload.get("repository") or {}).get("full_name", "")
        sender = (payload.get("sender") or {}).get("login", "")
        action = self._action(payload, default="updated")

        return ProcessedEvent(
            event_id=event_id or self._derive_id(payload, issue, action),
            event_type=f"{self.event_type}.{action}",
            repository=repo,
            actor=sender,
            source="github",
            issue={
                "number": issue.get("number"),
                "title": issue.get("title", ""),
                "state": issue.get("state", ""),
                "body": issue.get("body", ""),
                "html_url": issue.get("html_url", ""),
                "labels": [
                    label.get("name", "")
                    for label in issue.get("labels") or []
                    if isinstance(label, dict)
                ],
                "action": action,
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("issue")) and not payload.get("pull_request")

    @staticmethod
    def _derive_id(payload: dict[str, Any], issue: dict[str, Any], action: str) -> str:
        delivery = payload.get("delivery_id")
        if delivery:
            return str(delivery)
        number = issue.get("number", "unknown")
        repo = payload.get("repository", {}).get("full_name", "unknown")
        return f"issue-{repo}-{number}-{action}"
