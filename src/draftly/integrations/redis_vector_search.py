"""Redis-backed vector search for memory embeddings (RediSearch HNSW)."""

from __future__ import annotations

import struct
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

PREFIX = "draftly:vec:"


def _key(org_id: str, memory_item_id: str) -> str:
    return f"{PREFIX}{org_id}:{memory_item_id}"


def _index_name(org_id: str) -> str:
    return f"draftly:vectors:{org_id}"


def _encode_vector(embedding: list[float]) -> bytes:
    """Encode float vector as bytes for Redis storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


class RedisVectorSearch:
    """Store and search vector embeddings in Redis with per-org indexes."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def ensure_index(self, org_id: str) -> None:
        """Create the RediSearch index for an org if it doesn't exist."""
        try:
            await self._client.execute_command("FT.INFO", _index_name(org_id))
        except Exception:
            try:
                await self._client.execute_command(
                    "FT.CREATE",
                    _index_name(org_id),
                    "ON",
                    "HASH",
                    "PREFIX",
                    "1",
                    f"{PREFIX}{org_id}:",
                    "SCHEMA",
                    "memory_item_id",
                    "TAG",
                    "namespace",
                    "TAG",
                    "importance",
                    "NUMERIC",
                    "created_at",
                    "NUMERIC",
                )
            except Exception:
                logger.warning("redis_ft_create_failed org_id=%s", org_id, exc_info=True)

    async def store(
        self,
        org_id: str,
        memory_item_id: str,
        namespace: str,
        embedding: list[float],
        importance: float = 0.5,
    ) -> None:
        """Store an embedding in Redis."""
        key = _key(org_id, memory_item_id)
        try:
            await self._client.hset(
                key,
                mapping={
                    "memory_item_id": memory_item_id,
                    "namespace": namespace,
                    "importance": str(importance),
                    "created_at": str(time.time()),
                },
            )
        except Exception:
            logger.warning("redis_vector_store_failed item=%s", memory_item_id, exc_info=True)

    async def delete(self, org_id: str, memory_item_id: str) -> None:
        """Remove an embedding from Redis."""
        key = _key(org_id, memory_item_id)
        try:
            await self._client.delete(key)
        except Exception:
            pass

    async def search(
        self,
        org_id: str,
        namespace: str,
        embedding: list[float],
        limit: int = 5,
    ) -> list[dict]:
        """Search for similar vectors. Returns empty list on failure."""
        try:
            result = await self._client.execute_command(
                "FT.SEARCH",
                _index_name(org_id),
                f"@namespace:{{{namespace}}}",
                "LIMIT",
                "0",
                str(limit),
            )
            if not result or len(result) < 2:
                return []
            items = []
            for i in range(1, len(result), 2):
                fields = result[i + 1]
                item = {}
                for j in range(0, len(fields), 2):
                    item[fields[j]] = fields[j + 1]
                items.append(item)
            return items
        except Exception:
            logger.warning("redis_vector_search_failed org=%s", org_id, exc_info=True)
            return []
