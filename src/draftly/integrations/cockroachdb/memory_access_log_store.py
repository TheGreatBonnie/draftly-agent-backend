from __future__ import annotations

from typing import Any

from integrations.cockroachdb.client import CockroachDBClient


class MemoryAccessLogStore:
    def __init__(
        self,
        client: CockroachDBClient | None = None,
    ) -> None:
        self.client = client or CockroachDBClient()

    async def insert(
        self,
        *,
        org_id: str,
        memory_item_id: str,
        agent: str | None = None,
        workflow: str | None = None,
        query: str | None = None,
        similarity_score: float | None = None,
        rank: int | None = None,
        used: bool = False,
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO memory_access_log (
                org_id,
                memory_item_id,
                agent,
                workflow,
                query,
                similarity_score,
                rank,
                used
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, org_id, memory_item_id, query
            """,
            org_id,
            memory_item_id,
            agent,
            workflow,
            query,
            similarity_score,
            rank,
            used,
        )

        return dict(row)
