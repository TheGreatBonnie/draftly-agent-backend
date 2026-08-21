"""Discord workflow repository operations."""
from __future__ import annotations

from typing import cast

from draftly.integrations.database.client import DatabaseClient


async def save_discord_workflow(
    *,
    org_id: str,
    workflow_id: str,
    channel_id: str,
    message_id: str,
    thread_id: str,
    source_message: str,
    db: DatabaseClient | None = None,
) -> str:
    """Save or update a Discord workflow record."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """INSERT INTO discord_workflows
           (org_id, workflow_id, channel_id, message_id, thread_id, source_message, status)
           VALUES ($1, $2, $3, $4, $5, $6, 'pending')
           ON CONFLICT (workflow_id) DO UPDATE SET
               status = EXCLUDED.status,
               updated_at = now()
           RETURNING id::text""",
        org_id,
        workflow_id,
        channel_id,
        message_id,
        thread_id,
        source_message,
    )
    if row is None:
        raise RuntimeError("discord workflow row missing after insert")
    return cast(str, row["id"])
