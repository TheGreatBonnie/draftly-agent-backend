"""Organizations repository operations."""
from __future__ import annotations

from typing import Any

import structlog

from integrations.cockroachdb.client import CockroachDBClient

logger = structlog.get_logger()


async def update_org_github(
    *,
    org_id: str,
    github_org: str,
    db: CockroachDBClient | None = None,
) -> None:
    """Update organization's GitHub org."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    await db.execute(
        "UPDATE organizations SET github_org = $1 WHERE clerk_org_id = $2",
        github_org,
        org_id,
    )


async def get_org_by_clerk_id(
    *,
    clerk_org_id: str,
    db: CockroachDBClient | None = None,
) -> dict[str, Any] | None:
    """Find organization by Clerk org ID."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    row = await db.fetch_one(
        (
            "SELECT clerk_org_id, clerk_org_name, github_org, "
            "slack_workspace_id, discord_guild_id, discord_trigger_channels "
            "FROM organizations WHERE clerk_org_id = $1"
        ),
        clerk_org_id,
    )
    return dict(row) if row else None


async def get_org_by_github_org(
    *,
    github_org: str,
    db: CockroachDBClient | None = None,
) -> dict[str, Any] | None:
    """Find organization by GitHub org name."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    row = await db.fetch_one(
        "SELECT clerk_org_id, clerk_org_name, github_org "
        "FROM organizations WHERE github_org = $1",
        github_org,
    )
    return dict(row) if row else None


async def get_org_by_slack_team(
    *,
    team_id: str,
    db: CockroachDBClient | None = None,
) -> dict[str, Any] | None:
    """Find organization by Slack team ID."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    row = await db.fetch_one(
        (
            "SELECT o.clerk_org_id, o.clerk_org_name, o.github_org, "
            "o.slack_workspace_id, si.team_id, si.team_name "
            "FROM organizations o "
            "JOIN slack_installations si ON si.org_id = o.clerk_org_id "
            "WHERE si.team_id = $1"
        ),
        team_id,
    )
    return dict(row) if row else None


async def get_org_by_discord_guild(
    *,
    guild_id: str,
    db: CockroachDBClient | None = None,
) -> dict[str, Any] | None:
    """Find organization by Discord guild ID."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    row = await db.fetch_one(
        (
            "SELECT clerk_org_id, clerk_org_name, "
            "discord_guild_id, discord_trigger_channels "
            "FROM organizations WHERE discord_guild_id = $1"
        ),
        guild_id,
    )
    return dict(row) if row else None


async def get_or_create_org_by_clerk(
    *,
    clerk_org_id: str,
    name: str,
    db: CockroachDBClient | None = None,
) -> str:
    """Get or create an organization from a Clerk webhook. Returns clerk_org_id."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    existing = await db.fetch_one(
        "SELECT clerk_org_id FROM organizations WHERE clerk_org_id = $1",
        clerk_org_id,
    )
    if existing:
        return str(existing["clerk_org_id"])

    existing = await db.fetch_one(
        "SELECT clerk_org_id FROM organizations WHERE clerk_org_name = $1",
        name,
    )
    if existing:
        await db.execute(
            "UPDATE organizations SET clerk_org_id = $1 WHERE clerk_org_id = $2",
            clerk_org_id,
            existing["clerk_org_id"],
        )
        return clerk_org_id

    try:
        row = await db.fetch_one(
            "INSERT INTO organizations (clerk_org_name, clerk_org_id) "
            "VALUES ($1, $2) RETURNING clerk_org_id",
            name,
            clerk_org_id,
        )
    except Exception:
        existing = await db.fetch_one(
            "SELECT clerk_org_id FROM organizations WHERE clerk_org_id = $1",
            clerk_org_id,
        )
        if existing is None:
            raise
        return str(existing["clerk_org_id"])

    logger.info("org_created_from_clerk", name=name, clerk_org_id=clerk_org_id)
    if row is None:
        raise RuntimeError("organization row missing after insert")
    return str(row["clerk_org_id"])
