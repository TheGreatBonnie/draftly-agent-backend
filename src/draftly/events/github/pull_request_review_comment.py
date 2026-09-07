"""Normalize GitHub pull-request review-comment webhooks."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent


class PullRequestReviewCommentProcessor(BaseProcessor):
    event_type = "pull_request_review_comment"

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        pull_request = payload.get("pull_request") or {}
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
                "source_url": comment.get("html_url") or pull_request.get("html_url"),
                "pull_request_number": pull_request.get("number"),
                "pull_request_url": pull_request.get("html_url"),
                "path": comment.get("path"),
                "line": comment.get("line"),
                "category": "review_comment",
                "action": action,
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("pull_request") and payload.get("comment"))

    @staticmethod
    def _derive_id(payload: dict[str, Any]) -> str:
        pull_request = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name", "unknown")
        return f"pull-request-review-comment-{repo}-{pull_request.get('number', 'unknown')}"
