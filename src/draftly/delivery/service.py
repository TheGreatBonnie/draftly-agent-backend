"""Delivery service (plan §8.6) — coordinate delivery across surfaces."""

from __future__ import annotations

from typing import Any

from draftly.delivery.discord import DiscordDelivery
from draftly.delivery.documentation import DocumentationDelivery
from draftly.delivery.github import GitHubDelivery
from draftly.delivery.models import PullRequestResult
from draftly.delivery.slack import SlackDelivery


class DeliveryService:
    """Single entry point for all delivery surfaces."""

    def __init__(
        self,
        github: GitHubDelivery | None = None,
        slack: SlackDelivery | None = None,
        discord: DiscordDelivery | None = None,
        documentation: DocumentationDelivery | None = None,
    ) -> None:
        self.github = github or GitHubDelivery()
        self.slack = slack or SlackDelivery()
        self.discord = discord or DiscordDelivery()
        self.documentation = documentation or DocumentationDelivery(self.github)

    async def deliver(
        self,
        *,
        surface: str,
        **kwargs: Any,
    ) -> Any:
        """Route a delivery request to the right surface handler."""
        if surface == "github":
            return await self.github.deliver(**kwargs)
        if surface == "slack":
            return await self.slack.post_message(**kwargs)
        if surface == "discord":
            return await self.discord.post_message(**kwargs)
        if surface == "documentation":
            return await self.documentation.deliver_plan(**kwargs)
        raise ValueError(f"Unknown delivery surface: {surface}")

    async def deliver_documentation(self, **kwargs: Any) -> PullRequestResult:
        return await self.documentation.deliver_plan(**kwargs)
