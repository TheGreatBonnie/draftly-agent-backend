from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from draftly.integrations.database.memory_store import DatabaseMemoryStore
from draftly.integrations.database.vector_search import VectorSearch


class MemoryRepository:
    def __init__(
        self,
        store: DatabaseMemoryStore | None = None,
        vector_search: VectorSearch | None = None,
    ) -> None:
        self.store = store or DatabaseMemoryStore()
        self.vector_search = vector_search or VectorSearch()

    async def create(
        self,
        *,
        namespace: str,
        content: str,
        memory_type: str,
        importance: float,
        metadata: dict[str, Any],
        embedding: Sequence[float],
        org_id: str | None = None,
        confidence: float = 0.5,
    ) -> dict[str, Any]:
        return await self.store.insert(
            org_id=org_id,
            namespace=namespace,
            content=content,
            memory_type=memory_type,
            importance=importance,
            confidence=confidence,
            metadata=metadata,
            embedding=embedding,
        )

    async def create_batch(
        self,
        *,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return await self.store.insert_batch(items=items)

    async def get(self, *, memory_id: str) -> dict[str, Any] | None:
        return await self.store.get(memory_id=memory_id)

    async def semantic_search(
        self,
        *,
        namespace: str,
        embedding: Sequence[float],
        limit: int = 10,
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.vector_search.search(
            namespace=namespace,
            embedding=embedding,
            limit=limit,
            org_id=org_id,
        )

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
        return await self.store.update(
            memory_id=memory_id,
            content=content,
            importance=importance,
            confidence=confidence,
            metadata=metadata,
            embedding=embedding,
        )

    async def delete(self, memory_id: str) -> bool:
        return await self.store.delete(memory_id=memory_id)

    async def set_status(self, *, memory_id: str, status: str) -> bool:
        return bool(await self.store.set_status(memory_id=memory_id, status=status))

    async def record_provenance(
        self,
        *,
        memory_id: str,
        source_type: str,
        source_id: str | None = None,
        source_url: str | None = None,
        evidence: list | None = None,
        org_id: str | None = None,
    ) -> None:
        from draftly.integrations.database.memory_sources_store import (
            MemorySourcesStore,
        )

        await MemorySourcesStore(client=self.store.client).insert(
            org_id=org_id or "",
            memory_item_id=memory_id,
            source_type=source_type,
            source_id=source_id,
            source_url=source_url,
            evidence="; ".join(evidence) if evidence else None,
        )

    async def delete_by_metadata(
        self,
        *,
        namespace: str,
        key: str,
        value: str,
        org_id: str | None = None,
    ) -> int:
        """Delete items in a namespace matching metadata[key]; returns count."""
        if org_id:
            return await self.store.delete_by_metadata_bulk(
                namespace=namespace, key=key, value=value, org_id=org_id
            )

        items = await self.list_namespace(namespace=namespace)
        deleted = 0
        for item in items:
            if (item.get("metadata") or {}).get(key) == value:
                if await self.delete(memory_id=item["id"]):
                    deleted += 1
        return deleted

    async def list_namespace(
        self,
        *,
        namespace: str,
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.store.list_namespace(namespace=namespace, org_id=org_id)

    async def record_access(
        self,
        *,
        org_id: str,
        memory_id: str,
        query: str | None = None,
        similarity_score: float | None = None,
        rank: int | None = None,
        agent: str | None = None,
        workflow: str | None = None,
        used: bool = False,
    ) -> dict[str, Any]:
        from draftly.integrations.database.memory_access_log_store import (
            MemoryAccessLogStore,
        )

        return await MemoryAccessLogStore(
            client=self.store.client,
        ).insert(
            org_id=org_id,
            memory_item_id=memory_id,
            query=query,
            similarity_score=similarity_score,
            rank=rank,
            agent=agent,
            workflow=workflow,
            used=used,
        )

    async def record_feedback(
        self,
        *,
        org_id: str,
        memory_id: str,
        feedback_type: str,
        source: str | None = None,
        score: float = 0.5,
        comment: str | None = None,
    ) -> dict[str, Any]:
        from draftly.integrations.database.memory_feedback_store import (
            MemoryFeedbackStore,
        )

        return await MemoryFeedbackStore(
            client=self.store.client,
        ).insert(
            org_id=org_id,
            memory_item_id=memory_id,
            feedback_type=feedback_type,
            source=source,
            score=score,
            comment=comment,
        )
