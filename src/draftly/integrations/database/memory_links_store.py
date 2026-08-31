from __future__ import annotations

from typing import Any

from draftly.integrations.database.client import DatabaseClient


class MemoryLinksStore:
    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def insert(
        self,
        *,
        org_id: str,
        source_memory_id: str,
        target_memory_id: str,
        relationship: str,
        confidence: float = 0.5,
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO memory_links (
                org_id,
                source_memory_id,
                target_memory_id,
                relationship,
                confidence
            )
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, org_id, source_memory_id, target_memory_id, relationship
            """,
            org_id,
            source_memory_id,
            target_memory_id,
            relationship,
            confidence,
        )

        return dict(row)

    async def list_by_memory(
        self,
        *,
        org_id: str | None,
        memory_item_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            """
            SELECT id, org_id, source_memory_id, target_memory_id,
                   relationship, confidence, created_at
            FROM memory_links
            WHERE source_memory_id = $1 OR target_memory_id = $1
            ORDER BY created_at DESC
            """,
            memory_item_id,
        )
        return [dict(r) for r in rows]
