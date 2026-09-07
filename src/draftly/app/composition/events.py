"""Draftly event composition (plan §7.3).

``EventComposition`` is the single entry point for raw webhook payloads
→ normalized events → graph surface routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from draftly.events.dispatcher import EventDispatcher
from draftly.events.github.issue import IssueProcessor
from draftly.events.github.issue_comment import IssueCommentProcessor
from draftly.events.github.pull_request import PullRequestProcessor
from draftly.events.github.pull_request_review import PullRequestReviewProcessor
from draftly.events.github.pull_request_review_comment import PullRequestReviewCommentProcessor
from draftly.events.github.push import PushProcessor
from draftly.events.github.release import ReleaseProcessor
from draftly.events.support.discord import DiscordProcessor
from draftly.events.support.slack import SlackProcessor


@dataclass(frozen=True)
class EventComposition:
    """Single entry point for raw webhook payloads → normalized events."""

    dispatcher: EventDispatcher = field(default_factory=EventDispatcher)
    pull_request: Any = None
    issue: Any = None
    issue_comment: Any = None
    release: Any = None
    push: Any = None
    pull_request_review: Any = None
    pull_request_review_comment: Any = None
    slack: Any = None
    discord: Any = None

    async def normalize_github(self, payload: dict) -> dict:
        """Route a raw GitHub webhook to the matching normalizer."""
        if self.issue_comment is not None and self.issue_comment.supports(payload):
            return (await self.issue_comment.process(payload)).model_dump()
        if (
            self.pull_request_review_comment is not None
            and self.pull_request_review_comment.supports(payload)
        ):
            return (await self.pull_request_review_comment.process(payload)).model_dump()
        if self.pull_request_review is not None and self.pull_request_review.supports(payload):
            return (await self.pull_request_review.process(payload)).model_dump()
        if payload.get("pull_request"):
            return (await self.pull_request.process(payload)).model_dump()
        if payload.get("issue") and not payload.get("pull_request"):
            return (await self.issue.process(payload)).model_dump()
        if payload.get("release"):
            return (await self.release.process(payload)).model_dump()
        if "ref" in payload and "commits" in payload:
            return (await self.push.process(payload)).model_dump()
        action = payload.get("action", "")
        raise ValueError(f"Unhandled GitHub payload action={action}")

    async def normalize_slack(self, payload: dict) -> dict:
        return (await self.slack.process(payload)).model_dump()

    async def normalize_discord(self, payload: dict) -> dict:
        return (await self.discord.process(payload)).model_dump()

    def workflow_type_for(self, event: dict) -> str | None:
        """Map a normalized event to its graph surface."""
        return self.dispatcher.route(event)

    def content_source_for(self, event: dict) -> dict[str, Any] | None:
        """Return a normalized content request payload for eligible GitHub events."""
        body = event.get("release") or event.get("pull_request") or {}
        if not (event.get("content_relevant") or body.get("content_relevant")):
            return None
        return {
            "repository_id": event.get("repository") or "",
            "source_event_id": body.get("source_event_id") or event.get("event_id"),
            "source_event_type": body.get("source_event_type"),
            "source_title": body.get("source_title") or body.get("title") or "GitHub update",
            "source_summary": body.get("source_summary") or body.get("title") or "",
            "source_evidence": body.get("source_evidence") or [],
        }


def build_event_system(
    *,
    workflows: Any = None,
    bus: Any = None,
) -> EventComposition:
    """Build the event composition with all processors registered."""
    del workflows, bus

    composition = EventComposition(
        pull_request=PullRequestProcessor(),
        issue=IssueProcessor(),
        issue_comment=IssueCommentProcessor(),
        release=ReleaseProcessor(),
        push=PushProcessor(),
        pull_request_review=PullRequestReviewProcessor(),
        pull_request_review_comment=PullRequestReviewCommentProcessor(),
        slack=SlackProcessor(),
        discord=DiscordProcessor(),
    )
    return composition
