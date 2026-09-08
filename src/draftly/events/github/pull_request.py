"""PullRequestProcessor: GitHub PR webhook → normalized PR event."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.events.base import BaseProcessor, ProcessedEvent
from draftly.events.types import EventType

logger = structlog.get_logger(__name__)


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
        if action == "closed" and pr.get("merged"):
            action = "merged"
        labels = {
            str(label.get("name", "")).lower()
            for label in (pr.get("labels") or [])
            if isinstance(label, dict)
        }
        content_relevant = action == "merged" and "content-relevant" in labels

        pull_request_fields: dict[str, Any] = {
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "state": pr.get("state", ""),
            "sha": (pr.get("head") or {}).get("sha", ""),
            "base": (pr.get("base") or {}).get("ref", ""),
            "html_url": pr.get("html_url", ""),
            "action": action,
            "content_relevant": content_relevant,
            "source_event_type": "pull_request",
            "source_event_id": str(pr.get("id") or event_id or ""),
            "source_title": pr.get("title", ""),
            "source_summary": pr.get("body") or pr.get("title", ""),
            "source_evidence": ([{"source_id": pr.get("html_url")}] if pr.get("html_url") else []),
        }
        # Forward optional diff evidence shipped by real GitHub (or the crafted
        # webhook) payloads so the run grounds in the true changed files even
        # when no git checkout is available. Empty values stay absent.
        for key in ("changed_files", "changed_file_details", "file_actions", "diff"):
            if pr.get(key):
                pull_request_fields[key] = pr[key]

        resolved_id = event_id or self._derive_id(payload, pr, action)
        logger.info(
            "github_pr_normalized",
            event_id=resolved_id,
            action=action,
            repository=repo,
            actor=sender,
            changed_files=len(pr.get("changed_files") or []),
            has_diff=bool(pr.get("diff")),
            content_relevant=content_relevant,
        )

        return ProcessedEvent(
            event_id=resolved_id,
            event_type=f"{self.event_type}.{action}",
            repository=repo,
            actor=sender,
            source="github",
            pull_request=pull_request_fields,
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
