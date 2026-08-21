from __future__ import annotations

from typing import Any

from draftly.integrations.database.client import DatabaseClient


class MemoryFeedbackStore:
    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def insert(
        self,
        *,
        org_id: str,
        memory_item_id: str,
        feedback_type: str,
        source: str | None = None,
        score: float = 0.5,
        comment: str | None = None,
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO memory_feedback (
                org_id,
                memory_item_id,
                feedback_type,
                source,
                score,
                comment
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, org_id, memory_item_id, feedback_type, score
            """,
            org_id,
            memory_item_id,
            feedback_type,
            source,
            score,
            comment,
        )

        return dict(row)
