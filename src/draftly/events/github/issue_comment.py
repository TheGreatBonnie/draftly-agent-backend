"""Normalize GitHub issue-comment feedback webhooks."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent


class IssueCommentProcessor(BaseProcessor):
    event_type = "issue_comment"

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        repository = payload.get("repository") or {}
        sender = payload.get("sender") or {}
        action = self._action(payload, default="created")
        comment_id = str(comment.get("id") or event_id or self._derive_id(payload))
        return ProcessedEvent(
            event_id=event_id or comment_id,
            event_type=f"{self.event_type}.{action}",
            repository=repository.get("full_name"),
            actor=sender.get("login"),
            source="github",
            feedback={
                "platform": "github",
                "content": comment.get("body") or "",
                "source_event_id": comment_id,
                "source_message_id": comment_id,
                "source_url": comment.get("html_url") or issue.get("html_url"),
                "issue_number": issue.get("number"),
                "issue_url": issue.get("html_url"),
                "category": "question",
                "action": action,
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("issue") and payload.get("comment"))

    @staticmethod
    def _derive_id(payload: dict[str, Any]) -> str:
        issue = payload.get("issue") or {}
        repo = (payload.get("repository") or {}).get("full_name", "unknown")
        return f"issue-comment-{repo}-{issue.get('number', 'unknown')}"
