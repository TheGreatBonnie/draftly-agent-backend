from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from draftly.integrations.database.client import DatabaseClient

_MEMORY_COLUMNS = """
    mi.id,
    mi.org_id,
    mi.namespace,
    mi.memory_type,
    mi.content,
    mi.summary,
    mi.status,
    mi.importance,
    mi.confidence,
    mi.version,
    mi.access_count,
    mi.last_accessed_at,
    mi.created_at,
    mi.updated_at
"""


class VectorSearch:
    """
    Async vector similarity search over memory_embeddings.
    """

    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def search(
        self,
        *,
        namespace: str,
        embedding: Sequence[float],
        limit: int = 10,
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        vector = self._format_vector(embedding)

        if org_id is not None:
            rows = await self.client.fetch_all(
                f"""
                SELECT
                    {_MEMORY_COLUMNS},
                    1 - (me.embedding <=> $1::VECTOR) AS similarity
                FROM memory_embeddings me
                JOIN memory_items mi ON mi.id = me.memory_item_id
                WHERE mi.namespace = $2
                  AND mi.org_id = $3
                  AND mi.status = 'active'
                ORDER BY me.embedding <=> $1::VECTOR
                LIMIT $4
                """,
                vector,
                namespace,
                org_id,
                limit,
            )
        else:
            rows = await self.client.fetch_all(
                f"""
                SELECT
                    {_MEMORY_COLUMNS},
                    1 - (me.embedding <=> $1::VECTOR) AS similarity
                FROM memory_embeddings me
                JOIN memory_items mi ON mi.id = me.memory_item_id
                WHERE mi.namespace = $2
                  AND mi.status = 'active'
                ORDER BY me.embedding <=> $1::VECTOR
                LIMIT $3
                """,
                vector,
                namespace,
                limit,
            )

        return [self._row_to_memory(row) for row in rows]

    @staticmethod
    def _row_to_memory(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "org_id": (str(row["org_id"]) if row["org_id"] else None),
            "namespace": row["namespace"],
            "memory_type": row["memory_type"],
            "content": row["content"],
            "summary": row["summary"],
            "status": row["status"],
            "importance": row["importance"],
            "confidence": row["confidence"],
            "version": row["version"],
            "access_count": row["access_count"],
            "last_accessed_at": row["last_accessed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "similarity": float(row["similarity"]),
        }

    @staticmethod
    def _format_vector(embedding: Sequence[float]) -> str:
        return "[" + ",".join(str(float(value)) for value in embedding) + "]"
