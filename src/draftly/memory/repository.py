"""Memory repository (plan §8.1) — domain wrapper over persistence.

Owns namespace conventions and converts between domain models and the
persistence layer's dict records.
"""

from __future__ import annotations

from typing import Any

from draftly.memory.embeddings import EmbeddingService
from draftly.persistence.repositories.memory import MemoryRepository


class MemoryNamespaces:
    """Canonical memory namespaces."""

    DOCUMENTS = "documents"
    CONVERSATIONS = "conversations"
    QUESTIONS = "questions"
    SOLUTIONS = "solutions"
    ISSUES = "issues"
    KNOWLEDGE = "knowledge"
    FEEDBACK = "feedback"
    PROJECTS = "projects"


class DomainMemoryRepository:
    """Store/retrieve typed memory items with embeddings handled here."""

    def __init__(
        self,
        repository: MemoryRepository | None = None,
        embeddings: EmbeddingService | None = None,
    ) -> None:
        self.repository = repository or MemoryRepository()
        self.embeddings = embeddings or EmbeddingService()

    async def store(self, item: Any) -> dict[str, Any]:
        """Persist a MemoryItem; embedding is generated here."""
        embedding = self.embeddings.embed(item.content)
        return await self.repository.create(
            namespace=item.namespace,
            content=item.content,
            memory_type=item.memory_type,
            importance=float(item.importance),
            confidence=float(item.confidence),
            metadata=dict(item.metadata),
            embedding=embedding,
            org_id=item.org_id,
        )

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        return await self.repository.get(memory_id=memory_id)

    async def search(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        embedding = self.embeddings.embed(query)
        return await self.repository.semantic_search(
            namespace=namespace,
            embedding=embedding,
            limit=limit,
        )

    async def update(self, memory_id: str, **fields: Any) -> dict[str, Any] | None:
        if "content" in fields and fields["content"]:
            fields["embedding"] = self.embeddings.embed(fields["content"])
        return await self.repository.update(memory_id=memory_id, **fields)

    async def delete(self, memory_id: str) -> bool:
        return await self.repository.delete(memory_id=memory_id)

    async def set_status(self, *, memory_id: str, status: str) -> bool:
        """Transition a memory record's lifecycle status (active/superseded/archived)."""
        return bool(
            await self.repository.set_status(memory_id=memory_id, status=status)
        )

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
        """Record an evidence/provenance row for a memory item."""
        await self.repository.record_provenance(
            memory_id=memory_id,
            source_type=source_type,
            source_id=source_id,
            source_url=source_url,
            evidence=evidence or [],
            org_id=org_id,
        )

    async def delete_by_metadata(
        self,
        *,
        namespace: str,
        key: str,
        value: str,
        org_id: str | None = None,
    ) -> int:
        """Delete items in a namespace whose metadata[key] == value, scoped to org."""
        items = await self.repository.list_namespace(namespace=namespace)
        deleted = 0
        for item in items:
            if org_id and item.get("org_id") != org_id:
                continue
            if (item.get("metadata") or {}).get(key) == value:
                if await self.repository.delete(item["id"]):
                    deleted += 1
        return deleted

    async def store_batch(self, items: list[Any]) -> list[dict[str, Any]]:
        """Persist many MemoryItems with a single embed_batch call."""
        if not items:
            return []
        embeddings = self.embeddings.embed_batch([item.content for item in items])
        results: list[dict[str, Any]] = []
        for item, embedding in zip(items, embeddings):
            results.append(
                await self.repository.create(
                    namespace=item.namespace,
                    content=item.content,
                    memory_type=item.memory_type,
                    importance=float(item.importance),
                    confidence=float(item.confidence),
                    metadata=dict(item.metadata),
                    embedding=embedding,
                    org_id=item.org_id,
                )
            )
        return results

    async def list_namespace(self, namespace: str) -> list[dict[str, Any]]:
        return await self.repository.list_namespace(namespace=namespace)
