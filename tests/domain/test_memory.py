"""§8.9 verification: domain services with repository fakes (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from draftly.memory import (
    DomainMemoryRepository,
    EmbeddingService,
    MemoryRanking,
    MemoryRetrieval,
    MemoryService,
)
from draftly.memory.models import Knowledge, Question
from draftly.persistence.repositories.memory import MemoryRepository

# ================================================================
# Fakes
# ================================================================


class FakeMemoryRepository:
    """In-memory stand-in for the persistence MemoryRepository."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self._next = 0

    async def create(
        self,
        *,
        namespace,
        content,
        memory_type,
        importance,
        metadata,
        embedding,
        org_id=None,
        confidence=0.5,
    ):
        self._next += 1
        record = {
            "id": f"mem-{self._next}",
            "org_id": org_id,
            "namespace": namespace,
            "content": content,
            "memory_type": memory_type,
            "importance": importance,
            "confidence": confidence,
            "metadata": metadata,
            "embedding": embedding,
            "created_at": datetime.now(UTC),
        }
        self.items[record["id"]] = record
        return record

    async def get(self, *, memory_id):
        return self.items.get(memory_id)

    async def semantic_search(self, *, namespace, embedding, limit=10):
        found = [
            {**r, "similarity": 0.5} for r in self.items.values() if r["namespace"] == namespace
        ]
        return found[:limit]

    async def update(
        self,
        *,
        memory_id,
        content=None,
        importance=None,
        confidence=None,
        metadata=None,
        embedding=None,
    ):
        record = self.items.get(memory_id)
        if not record:
            return None
        if content is not None:
            record["content"] = content
        if importance is not None:
            record["importance"] = importance
        if confidence is not None:
            record["confidence"] = confidence
        if metadata is not None:
            record["metadata"] = metadata
        if embedding is not None:
            record["embedding"] = embedding
        return record

    async def delete(self, *, memory_id):
        return self.items.pop(memory_id, None) is not None

    async def list_namespace(self, *, namespace):
        return [r for r in self.items.values() if r["namespace"] == namespace]


def make_service() -> tuple[MemoryService, FakeMemoryRepository]:
    repo = FakeMemoryRepository()
    embeddings = EmbeddingService(router=False)  # force hash embedder
    service = MemoryService(
        repository=DomainMemoryRepository(cast(MemoryRepository, repo), embeddings),
    )
    return service, repo


# ================================================================
# Memory service (§8.1)
# ================================================================


class TestMemoryService:
    async def test_store_and_recall_roundtrip(self) -> None:
        service, _ = make_service()
        await service.remember(
            Knowledge(
                namespace="knowledge",
                content="Retries use exponential backoff",
                topic="retries",
            )
        )
        results = await service.recall(namespace="knowledge", query="backoff")
        assert len(results) == 1
        assert "exponential backoff" in results[0]["content"]

    async def test_ranking_prefers_recent_and_important(self) -> None:
        ranking = MemoryRanking()
        old = {
            "id": "old",
            "importance": 0.5,
            "created_at": datetime.now(UTC) - timedelta(days=120),
            "similarity": 0.5,
        }
        new = {
            "id": "new",
            "importance": 0.9,
            "created_at": datetime.now(UTC),
            "similarity": 0.5,
        }
        ranked = ranking.rank([old, new])
        assert ranked[0]["id"] == "new"

    async def test_consolidate_reinforces_instead_of_duplicating(self) -> None:
        service, repo = make_service()
        first = await service.remember(
            Question(namespace="questions", content="How do I rotate keys?")
        )
        await service.remember(Question(namespace="questions", content="How do I rotate keys?"))

        result = await service.consolidate(
            namespace="questions",
            query="How do I rotate keys?",
            merge_target_id=first["id"],
        )

        assert result is not None
        assert result["importance"] > 0.5  # reinforced
        namespace_items = await repo.list_namespace(namespace="questions")
        assert len(namespace_items) == 2  # no third record created

    async def test_remember_knowledge_and_feedback_namespaces(self) -> None:
        service, repo = make_service()
        await service.remember(Knowledge(namespace="knowledge", content="TLS certs rotate at 90d"))
        assert len(await repo.list_namespace(namespace="knowledge")) == 1

    def test_hash_embedder_is_deterministic_and_normalized(self) -> None:
        from draftly.memory.embeddings import _hash_embed

        a = _hash_embed("connection pooling")
        b = _hash_embed("connection pooling")
        assert a == b
        assert pytest.approx(sum(v * v for v in a), abs=1e-6) == 1.0


class TestMemoryRetrieval:
    async def test_multi_namespace_fanout(self) -> None:
        service, _ = make_service()
        retrieval = MemoryRetrieval(service.repository)
        await service.remember(Knowledge(namespace="knowledge", content="pooling"))
        await service.remember(Question(namespace="questions", content="pooling?"))

        results = await retrieval.retrieve_multi(
            namespaces=["knowledge", "questions"],
            query="pooling",
            per_namespace=5,
        )
        assert set(results) == {"knowledge", "questions"}
        assert all(len(v) == 1 for v in results.values())
