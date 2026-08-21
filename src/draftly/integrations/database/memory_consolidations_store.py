from __future__ import annotations

from typing import Any

from integrations.cockroachdb.client import CockroachDBClient


class MemoryConsolidationsStore:
    def __init__(
        self,
        client: CockroachDBClient | None = None,
    ) -> None:
        self.client = client or CockroachDBClient()

    async def insert(
        self,
        *,
        org_id: str | None = None,
        operation: str,
        target_memory_id: str | None = None,
        source_memory_ids: list[str] | None = None,
        reason: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO memory_consolidations (
                org_id,
                operation,
                target_memory_id,
                source_memory_ids,
                reason,
                model
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, org_id, operation
            """,
            org_id,
            operation,
            target_memory_id,
            source_memory_ids or [],
            reason,
            model,
        )

        return dict(row)
