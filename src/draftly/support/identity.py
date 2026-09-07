"""Support identity resolution: org-scoped event enrichment and delivery targets.

Normalized Slack/Discord support events arrive with a workspace/guild id as their
``project_id``. Ingress enrichment resolves that platform identity to the linked
Clerk organization id so support workflows key off org-scoped tenancy and never
launch with a team/guild id as their tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from draftly.persistence.repositories.organizations import (
    get_org_by_discord_guild,
    get_org_by_slack_team,
)

logger = structlog.get_logger()


class SupportIdentityError(Exception):
    """Raised when a support event lacks a resolvable organization identity."""


@dataclass(frozen=True)
class SupportDeliveryTarget:
    """Resolved destination for a support delivery: platform + org + thread route."""

    platform: str
    org_id: str
    channel_id: str | None = None
    thread_id: str | None = None
    source_message_id: str | None = None
    platform_account_id: str | None = None


async def enrich_support_event(
    event: dict[str, Any],
    db: Any = None,
) -> dict[str, Any]:
    """Resolve ``project_id``/``org_id`` to the linked Clerk org for a support event.

    Retains the platform identity keys (``team_id`` / ``guild_id``) so credentials
    can be looked up per-installation later. Raises ``SupportIdentityError`` when
    the platform identity is missing or not linked to an organization.
    """
    platform = str(event.get("source") or "")
    if platform == "slack":
        team_id = event.get("team_id") or event.get("project_id")
        if not team_id:
            raise SupportIdentityError("slack event is missing team_id")
        organization = await get_org_by_slack_team(team_id=str(team_id), db=db)
        if not organization:
            logger.warning("support_identity_unlinked", platform="slack", team_id=str(team_id))
            raise SupportIdentityError(
                f"slack team {team_id} is not linked to an organization"
            )
    elif platform == "discord":
        guild_id = event.get("guild_id") or event.get("project_id")
        if not guild_id:
            raise SupportIdentityError("discord event is missing guild_id")
        organization = await get_org_by_discord_guild(guild_id=str(guild_id), db=db)
        if not organization:
            logger.warning("support_identity_unlinked", platform="discord", guild_id=str(guild_id))
            raise SupportIdentityError(
                f"discord guild {guild_id} is not linked to an organization"
            )
    else:
        raise SupportIdentityError(f"Unsupported support platform: {platform!r}")

    org_id = str(organization["clerk_org_id"])
    enriched = dict(event)
    enriched["project_id"] = org_id
    enriched["org_id"] = org_id
    return enriched


async def resolve_support_target(
    event: dict[str, Any],
    db: Any = None,
) -> SupportDeliveryTarget:
    """Resolve a support event to an org-scoped delivery target."""
    enriched = await enrich_support_event(event, db=db)
    platform = str(enriched.get("source") or "")
    platform_account_id = enriched.get("team_id") or enriched.get("guild_id")
    return SupportDeliveryTarget(
        platform=platform,
        org_id=enriched["org_id"],
        channel_id=enriched.get("channel") or enriched.get("channel_id"),
        thread_id=enriched.get("thread_ts"),
        source_message_id=enriched.get("source_message_id"),
        platform_account_id=str(platform_account_id) if platform_account_id else None,
    )
