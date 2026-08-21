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

    async def list_namespace(self, namespace: str) -> list[dict[str, Any]]:
        return await self.repository.list_namespace(namespace=namespace)
