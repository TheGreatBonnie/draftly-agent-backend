"""Discord workflow repository operations."""

from __future__ import annotations

from typing import Any, cast

from draftly.delivery.models import SupportDeliveryReceipt
from draftly.integrations.database.client import DatabaseClient


class DiscordWorkflowRepository:
    """Persistence adapter for Discord workflow identity/receipt rows."""

    def __init__(self, db: DatabaseClient) -> None:
        self.db = db

    async def save_support_delivery(self, receipt: SupportDeliveryReceipt) -> str | None:
        return await save_support_delivery(receipt=receipt, db=self.db)

    async def get_support_delivery(self, run_id: str) -> dict[str, Any] | None:
        return await get_support_delivery(run_id=run_id, db=self.db)

    async def get_support_delivery_by_source(
        self, org_id: str, source_message_id: str
    ) -> dict[str, Any] | None:
        return await get_support_delivery_by_source(
            org_id=org_id,
            source_message_id=source_message_id,
            db=self.db,
        )


async def save_support_delivery(
    *,
    receipt: SupportDeliveryReceipt,
    db: DatabaseClient | None = None,
) -> str | None:
    """Persist a Discord delivery receipt (status delivered/failed/pending)."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """INSERT INTO discord_workflows
           (org_id, workflow_id, channel_id, thread_id, source_message,
            source_message_id, provider_message_id, delivery_error, status,
            delivered_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                   CASE WHEN $9::text = 'delivered' THEN now() ELSE NULL END)
           ON CONFLICT (workflow_id) DO UPDATE SET
               org_id = EXCLUDED.org_id,
               channel_id = EXCLUDED.channel_id,
               thread_id = EXCLUDED.thread_id,
               source_message = EXCLUDED.source_message,
               source_message_id = EXCLUDED.source_message_id,
               provider_message_id = EXCLUDED.provider_message_id,
               delivery_error = EXCLUDED.delivery_error,
               status = EXCLUDED.status,
               delivered_at = CASE
                   WHEN EXCLUDED.status = 'delivered' THEN now()
                   ELSE discord_workflows.delivered_at
               END,
               updated_at = now()
           RETURNING id::text""",
        receipt.org_id,
        receipt.run_id,
        receipt.channel_id,
        receipt.thread_id,
        receipt.source_message_id,
        receipt.source_message_id,
        receipt.provider_message_id,
        receipt.error,
        receipt.status,
    )
    if row is None:
        raise RuntimeError("discord delivery row missing after upsert")
    return cast(str, row["id"])


async def get_support_delivery(
    *,
    run_id: str,
    db: DatabaseClient | None = None,
) -> dict[str, Any] | None:
    """Fetch a Discord workflow/receipt row by run id."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """SELECT id::text, org_id, workflow_id, channel_id, thread_id,
                  source_message, source_message_id, provider_message_id,
                  delivery_error, status, delivered_at
           FROM discord_workflows
           WHERE workflow_id = $1""",
        run_id,
    )
    return dict(row) if row is not None else None


async def get_support_delivery_by_source(
    *,
    org_id: str,
    source_message_id: str,
    db: DatabaseClient | None = None,
) -> dict[str, Any] | None:
    """Fetch a Discord delivery receipt by organization/source event identity."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """SELECT id::text, org_id, workflow_id, channel_id, thread_id,
                  source_message, source_message_id, provider_message_id,
                  delivery_error, status, delivered_at
           FROM discord_workflows
           WHERE org_id = $1 AND source_message_id = $2
           ORDER BY created_at DESC
           LIMIT 1""",
        org_id,
        source_message_id,
    )
    return dict(row) if row is not None else None


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
