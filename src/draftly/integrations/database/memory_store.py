from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from draftly.integrations.database.client import DatabaseClient

_MEMORY_COLUMNS = """
    id,
    org_id,
    namespace,
    memory_type,
    content,
    summary,
    status,
    importance,
    confidence,
    version,
    access_count,
    last_accessed_at,
    created_at,
    updated_at
"""


class DatabaseMemoryStore:
    """
    Async memory store backed by memory_items + memory_embeddings.
    """

    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def insert(
        self,
        *,
        org_id: str,
        namespace: str,
        content: str,
        memory_type: str,
        importance: float,
        confidence: float = 0.5,
        metadata: dict[str, Any] | None = None,
        embedding: Sequence[float],
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
    ) -> dict[str, Any]:
        memory_id = str(uuid4())
        embedding_id = str(uuid4())

        item_params = (
            memory_id,
            org_id,
            namespace,
            memory_type,
            content,
            importance,
            confidence,
            metadata or {},
        )

        async with self.client.transaction() as conn:
            row = await self.client.fetch_one_conn(
                conn,
                f"""
                INSERT INTO memory_items (
                    id,
                    org_id,
                    namespace,
                    memory_type,
                    content,
                    importance,
                    confidence,
                    metadata
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8
                )
                RETURNING {_MEMORY_COLUMNS}
                """,
                *item_params,
            )

            await self.client.execute_conn(
                conn,
                """
                INSERT INTO memory_embeddings (
                    id,
                    memory_item_id,
                    org_id,
                    embedding,
                    model,
                    dimensions
                )
                VALUES (
                    $1, $2, $3, $4::VECTOR, $5, $6
                )
                """,
                embedding_id,
                memory_id,
                org_id,
                self._format_vector(embedding),
                model,
                dimensions,
            )

        return self._row_to_memory(row)

    async def get(
        self,
        *,
        memory_id: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            f"""
            SELECT {_MEMORY_COLUMNS}
            FROM memory_items
            WHERE id = $1
            """,
            memory_id,
        )

        return self._row_to_memory(row) if row else None

    async def update(
        self,
        *,
        memory_id: str,
        content: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        embedding: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        fields = []
        params: list[Any] = []

        if content is not None:
            fields.append("content = $1, version = version + 1")
            params.append(content)

        if importance is not None:
            fields.append(f"importance = ${len(params) + 1}")
            params.append(importance)

        if confidence is not None:
            fields.append(f"confidence = ${len(params) + 1}")
            params.append(confidence)

        if metadata is not None:
            fields.append(f"metadata = ${len(params) + 1}")
            params.append(metadata)

        if not fields:
            raise ValueError("No fields were provided for update.")

        params.append(memory_id)

        async with self.client.transaction() as conn:
            row = await self.client.fetch_one_conn(
                conn,
                f"""
                UPDATE memory_items
                SET {", ".join(fields)}
                WHERE id = ${len(params)}
                RETURNING {_MEMORY_COLUMNS}
                """,
                *params,
            )

            if row is None:
                raise ValueError(f"Memory '{memory_id}' was not found.")

            if embedding is not None:
                await self.client.execute_conn(
                    conn,
                    """
                    INSERT INTO memory_embeddings (
                        id,
                        memory_item_id,
                        org_id,
                        embedding,
                        model,
                        dimensions
                    )
                    VALUES (
                        gen_random_uuid(),
                        $1,
                        $2,
                        $3::VECTOR,
                        $4,
                        $5
                    )
                    """,
                    memory_id,
                    row["org_id"],
                    self._format_vector(embedding),
                    "text-embedding-3-small",
                    1536,
                )

        return self._row_to_memory(row)

    async def delete(
        self,
        *,
        memory_id: str,
    ) -> bool:
        await self.client.execute(
            """
            DELETE FROM memory_items
            WHERE id = $1
            """,
            memory_id,
        )

        return True

    async def list_namespace(
        self,
        *,
        namespace: str,
    ) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            f"""
            SELECT {_MEMORY_COLUMNS}
            FROM memory_items
            WHERE namespace = $1
            ORDER BY importance DESC
            """,
            namespace,
        )

        return [self._row_to_memory(row) for row in rows]

    @staticmethod
    def _row_to_memory(row) -> dict[str, Any]:
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
        }

    @staticmethod
    def _format_vector(embedding: Sequence[float]) -> str:
        return "[" + ",".join(str(float(value)) for value in embedding) + "]"
