"""Normalize GitHub pull-request review webhooks."""

from __future__ import annotations

from typing import Any

from draftly.events.base import BaseProcessor, ProcessedEvent


class PullRequestReviewProcessor(BaseProcessor):
    event_type = "pull_request_review"

    async def process(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> ProcessedEvent:
        pull_request = payload.get("pull_request") or {}
        review = payload.get("review") or {}
        repository = payload.get("repository") or {}
        sender = payload.get("sender") or {}
        action = self._action(payload, default="submitted")
        review_id = str(review.get("id") or event_id or self._derive_id(payload))
        return ProcessedEvent(
            event_id=event_id or review_id,
            event_type=f"{self.event_type}.{action}",
            repository=repository.get("full_name"),
            actor=sender.get("login"),
            source="github",
            feedback={
                "platform": "github",
                "content": review.get("body") or "",
                "source_event_id": review_id,
                "source_message_id": review_id,
                "source_url": review.get("html_url") or pull_request.get("html_url"),
                "pull_request_number": pull_request.get("number"),
                "pull_request_url": pull_request.get("html_url"),
                "review_state": review.get("state"),
                "category": "review",
                "action": action,
            },
        )

    def supports(self, payload: dict[str, Any]) -> bool:
        return bool(payload.get("pull_request") and payload.get("review"))

    @staticmethod
    def _derive_id(payload: dict[str, Any]) -> str:
        pull_request = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name", "unknown")
        return f"pull-request-review-{repo}-{pull_request.get('number', 'unknown')}"
