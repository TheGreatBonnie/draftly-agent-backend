"""Persistence repository for normalized feedback signals."""

from __future__ import annotations

from typing import Any

from draftly.feedback.models import FeedbackItem
from draftly.integrations.database.client import DatabaseClient


class FeedbackRepository:
    """Store and retrieve organization-scoped feedback signals."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    async def save_signal(self, item: FeedbackItem) -> FeedbackItem:
        """Upsert one signal using its organization/source identity."""
        if not item.org_id:
            raise ValueError("feedback signal requires org_id")
        source_event_id = item.source_event_id or item.source_message_id or item.id
        if not source_event_id:
            raise ValueError("feedback signal requires source_event_id")

        row = await self.database.fetch_one(
            """
            INSERT INTO feedback_signals (
                org_id, platform, source_event_id, source_message_id,
                source_url, content, topic, author, channel,
                category, sentiment, timestamp, raw
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12, $13
            )
            ON CONFLICT (org_id, platform, source_event_id)
            DO UPDATE SET
                source_message_id = EXCLUDED.source_message_id,
                source_url = EXCLUDED.source_url,
                content = EXCLUDED.content,
                topic = EXCLUDED.topic,
                author = EXCLUDED.author,
                channel = EXCLUDED.channel,
                category = EXCLUDED.category,
                sentiment = EXCLUDED.sentiment,
                timestamp = EXCLUDED.timestamp,
                raw = EXCLUDED.raw,
                updated_at = now()
            RETURNING id::text, org_id, platform, source_event_id,
                      source_message_id, source_url, content, topic,
                      author, channel, category, sentiment, timestamp
            """,
            item.org_id,
            item.platform,
            source_event_id,
            item.source_message_id,
            item.source_url,
            item.content,
            item.topic,
            item.author,
            item.channel,
            item.category,
            item.sentiment,
            item.timestamp,
            item.model_dump(mode="json"),
        )
        if row is None:
            raise RuntimeError("feedback signal missing after upsert")
        return FeedbackItem(**dict(row))

    async def list_recent(
        self,
        org_id: str,
        platform: str | None = None,
        limit: int = 200,
    ) -> list[FeedbackItem]:
        """Return recent signals belonging only to ``org_id``."""
        conditions = ["org_id = $1"]
        args: list[Any] = [org_id]
        if platform:
            conditions.append("platform = $2")
            args.append(platform)
            limit_placeholder = "$3"
        else:
            limit_placeholder = "$2"
        args.append(limit)

        rows = await self.database.fetch_all(
            f"""
            SELECT id::text, org_id, platform, source_event_id,
                   source_message_id, source_url, content, topic,
                   author, channel, category, sentiment, timestamp
            FROM feedback_signals
            WHERE {' AND '.join(conditions)}
            ORDER BY timestamp DESC NULLS LAST, created_at DESC
            LIMIT {limit_placeholder}
            """,
            *args,
        )
        return [FeedbackItem(**dict(row)) for row in rows]

    async def list_org_ids(self) -> list[str]:
        """List organizations eligible for an isolated feedback scan."""
        rows = await self.database.fetch_all(
            """
            SELECT clerk_org_id
            FROM organizations
            ORDER BY clerk_org_id
            """
        )
        return [str(row["clerk_org_id"]) for row in rows]
