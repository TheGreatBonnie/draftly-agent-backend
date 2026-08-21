"""Draftly event composition (plan §7.3).

``EventComposition`` is the single entry point for raw webhook payloads
→ normalized events → graph surface routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from draftly.events.dispatcher import EventDispatcher
from draftly.events.github.issue import IssueProcessor
from draftly.events.github.pull_request import PullRequestProcessor
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
    release: Any = None
    push: Any = None
    slack: Any = None
    discord: Any = None

    async def normalize_github(self, payload: dict) -> dict:
        """Route a raw GitHub webhook to the matching normalizer."""
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
        release=ReleaseProcessor(),
        push=PushProcessor(),
        slack=SlackProcessor(),
        discord=DiscordProcessor(),
    )
    return composition
