"""Memory service (plan §8.1) — storage/retrieval/curation facade."""

from __future__ import annotations

import logging
from typing import Any

from draftly.memory.models import MemoryItem
from draftly.memory.ranking import MemoryRanking
from draftly.memory.repository import DomainMemoryRepository, MemoryNamespaces
from draftly.memory.retrieval import MemoryRetrieval

logger = logging.getLogger(__name__)


class MemoryService:
    """High-level memory API used by workflows and domain services."""

    def __init__(
        self,
        repository: DomainMemoryRepository | None = None,
        ranking: MemoryRanking | None = None,
    ) -> None:
        self.repository = repository or DomainMemoryRepository()
        self.retrieval = MemoryRetrieval(self.repository, ranking)
        self.namespaces = MemoryNamespaces

    # --------------------------------------------------------------
    # Storage
    # --------------------------------------------------------------

    async def remember(self, item: MemoryItem) -> dict[str, Any]:
        """Store a typed memory item."""
        record = await self.repository.store(item)
        logger.debug(
            "memory_stored namespace=%s id=%s",
            item.namespace,
            record.get("id"),
        )
        return record

    async def forget(self, memory_id: str) -> bool:
        return await self.repository.delete(memory_id)

    # --------------------------------------------------------------
    # Retrieval
    # --------------------------------------------------------------

    async def recall(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        return await self.retrieval.retrieve(
            namespace=namespace,
            query=query,
            limit=limit,
        )

    async def recall_knowledge(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Grounding context: curated knowledge + validated solutions."""
        combined: list[dict[str, Any]] = []
        for namespace in (self.namespaces.KNOWLEDGE, self.namespaces.SOLUTIONS):
            found = await self.retrieval.retrieve(
                namespace=namespace,
                query=query,
                limit=limit,
            )
            for record in found:
                record["namespace"] = namespace
            combined.extend(found)
        return self.retrieval.ranking.rank(combined, limit=limit)

    # --------------------------------------------------------------
    # Curation
    # --------------------------------------------------------------

    async def consolidate(
        self,
        *,
        namespace: str,
        query: str,
        merge_target_id: str | None = None,
        min_similarity: float = 0.95,
    ) -> dict[str, Any] | None:
        """Merge near-duplicate memories into one canonical record.

        Finds records similar to ``query``; when one exceeds
        ``min_similarity`` its importance is reinforced instead of
        storing a duplicate.
        """
        duplicates = await self.retrieval.retrieve(
            namespace=namespace,
            query=query,
            limit=5,
            min_similarity=min_similarity,
        )
        target_id = merge_target_id or (duplicates[0]["id"] if duplicates else None)
        if not target_id:
            return None

        existing = await self.repository.get(target_id)
        if not existing:
            return None

        reinforced_importance = min(1.0, float(existing.get("importance", 0.5)) + 0.1)
        return await self.repository.update(
            target_id,
            importance=reinforced_importance,
        )

    async def curate_namespace(self, namespace: str) -> dict[str, int]:
        """Basic hygiene stats for a namespace."""
        items = await self.repository.list_namespace(namespace)
        return {
            "total": len(items),
            "high_importance": sum(1 for i in items if float(i.get("importance", 0)) >= 0.8),
        }
