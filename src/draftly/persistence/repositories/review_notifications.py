"""Per-recipient review notification delivery receipts.

Each (review, platform, recipient) delivery is claimed atomically before a
send so concurrent runner retries can never double-notify. A prior ``failed``
row is re-claimed for retry; ``claimed``/``sent`` rows are left untouched.
"""

from __future__ import annotations

from typing import Any

import structlog

from draftly.integrations.database.client import DatabaseClient

logger = structlog.get_logger(__name__)


class ReviewNotificationRepository:
    """NeonDB repository for review_notification_deliveries receipts."""

    def __init__(self, database: DatabaseClient | None = None):
        self.database = database or DatabaseClient()

    async def claim(
        self,
        review_id: str,
        org_id: str,
        platform: str,
        recipient_id: str,
    ) -> bool:
        """Atomically claim a delivery slot; False when already claimed/sent.

        A fresh attempt inserts a ``claimed`` row. A conflict re-claims a prior
        ``failed`` row (status flips to ``claimed``) and returns True; an
        already-``claimed`` or ``sent`` row returns False.
        """
        inserted = await self.database.fetch_one(
            """
            INSERT INTO review_notification_deliveries (
                review_id, org_id, platform, recipient_id, status
            ) VALUES ($1, $2, $3, $4, 'claimed')
            ON CONFLICT (review_id, platform, recipient_id) DO NOTHING
            RETURNING id
            """,
            review_id,
            org_id,
            platform,
            recipient_id,
        )
        if inserted:
            return True

        reclaimed = await self.database.fetch_one(
            """
            UPDATE review_notification_deliveries
            SET status = 'claimed', error = NULL, sent_at = NULL
            WHERE review_id = $1 AND platform = $2 AND recipient_id = $3
                  AND status = 'failed'
            RETURNING id
            """,
            review_id,
            platform,
            recipient_id,
        )
        return reclaimed is not None

    async def mark_sent(
        self,
        review_id: str,
        org_id: str,
        platform: str,
        recipient_id: str,
    ) -> None:
        """Record a successful delivery for the claimed receipt."""
        await self.database.execute(
            """
            UPDATE review_notification_deliveries
            SET status = 'sent', sent_at = now(), error = NULL
            WHERE review_id = $1 AND org_id = $2 AND platform = $3
                  AND recipient_id = $4
            """,
            review_id,
            org_id,
            platform,
            recipient_id,
        )

    async def mark_failed(
        self,
        review_id: str,
        org_id: str,
        platform: str,
        recipient_id: str,
        error: str | None = None,
    ) -> None:
        """Record a failed attempt; the delivery stays retryable."""
        await self.database.execute(
            """
            UPDATE review_notification_deliveries
            SET status = 'failed', error = $5
            WHERE review_id = $1 AND org_id = $2 AND platform = $3
                  AND recipient_id = $4
            """,
            review_id,
            org_id,
            platform,
            recipient_id,
            error,
        )

    async def get_delivery(
        self,
        review_id: str,
        platform: str,
        recipient_id: str,
    ) -> dict[str, Any] | None:
        row = await self.database.fetch_one(
            """
            SELECT * FROM review_notification_deliveries
            WHERE review_id = $1 AND platform = $2 AND recipient_id = $3
            """,
            review_id,
            platform,
            recipient_id,
        )
        return dict(row) if row else None
