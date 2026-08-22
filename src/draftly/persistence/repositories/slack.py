"""Slack workflow repository operations."""

from __future__ import annotations

from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient


async def save_slack_workflow(
    *,
    org_id: str,
    workflow_id: str,
    channel_id: str,
    thread_ts: str,
    source_message: str,
    db: DatabaseClient | None = None,
) -> str:
    """Save or update a Slack workflow record."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """INSERT INTO slack_workflows
           (org_id, workflow_id, channel_id, thread_ts, source_message, status)
           VALUES ($1, $2, $3, $4, $5, 'pending')
           ON CONFLICT (workflow_id) DO UPDATE SET
               status = EXCLUDED.status,
               updated_at = now()
           RETURNING id::text""",
        org_id,
        workflow_id,
        channel_id,
        thread_ts,
        source_message,
    )
    if row is None:
        raise RuntimeError("slack workflow row missing after insert")
    return cast(str, row["id"])


async def list_slack_installations(
    *,
    db: DatabaseClient | None = None,
) -> list[dict[str, Any]]:
    """List all Slack installations with org names."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    rows = await db.fetch_all(
        """SELECT si.id::text, si.team_id, si.team_name, si.bot_user_id,
                  si.org_id, si.installed_at, si.updated_at,
                  o.clerk_org_name as org_name
           FROM slack_installations si
           LEFT JOIN organizations o ON o.clerk_org_id = si.org_id
           ORDER BY si.installed_at DESC"""
    )
    return [dict(r) for r in rows]


async def link_slack_installation(
    *,
    team_id: str,
    org_id: str,
    db: DatabaseClient | None = None,
) -> None:
    """Link a Slack installation to a Clerk organization."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    await db.execute(
        "UPDATE slack_installations SET org_id = $1 WHERE team_id = $2",
        org_id,
        team_id,
    )


async def remove_slack_installation(
    *,
    team_id: str,
    db: DatabaseClient | None = None,
) -> None:
    """Delete a Slack installation record."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    await db.execute(
        "DELETE FROM slack_installations WHERE team_id = $1",
        team_id,
    )
