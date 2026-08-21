"""Live NeonDB round-trip (plan §11.4). Requires DRAFTLY_LIVE=1."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_memory_semantic_search_round_trip(db, requires_live):
    """Store a memory item → recall returns it (real pgvector embeddings)."""
    from draftly.integrations.database.memory_store import DatabaseMemoryStore
    from draftly.integrations.database.vector_search import VectorSearch
    from draftly.memory import DomainMemoryRepository, MemoryService
    from draftly.memory.models.base import MemoryItem
    from draftly.persistence.repositories.memory import MemoryRepository

    repo = MemoryRepository(
        store=DatabaseMemoryStore(db), vector_search=VectorSearch(db)
    )
    service = MemoryService(repository=DomainMemoryRepository(repo))

    record = await service.remember(
        MemoryItem(
            content="Draftly deploys docs previews on every PR",
            namespace="org:live-test",
        )
    )
    assert record.get("id")

    hits = await service.recall_knowledge("docs preview deployment", limit=3)

    assert hits, "expected the stored item back via pgvector search"
    assert any("previews" in str(h.get("content", "")) for h in hits)
