"""Unit tests for memory deletion scoped to a metadata value."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.memory_store import DatabaseMemoryStore
from draftly.memory.embeddings import EmbeddingService
from draftly.memory.repository import DomainMemoryRepository


@pytest.mark.asyncio
async def test_delete_by_metadata_delegates_to_repository():
    """Domain delete_by_metadata delegates to the persistence repository.

    Task 5 moved deletion into a single SQL DELETE scoped by
    (org_id, namespace, metadata->>key); the domain layer only threads
    arguments through and returns the deleted count.
    """
    repository = AsyncMock()
    repository.delete_by_metadata = AsyncMock(return_value=2)
    domain = DomainMemoryRepository(repository=repository)

    deleted = await domain.delete_by_metadata(
        namespace="documents",
        key="document_id",
        value="doc-1",
        org_id="org-1",
    )

    assert deleted == 2
    repository.delete_by_metadata.assert_awaited_once_with(
        namespace="documents", key="document_id", value="doc-1", org_id="org-1"
    )


@pytest.mark.asyncio
async def test_delete_by_metadata_returns_repository_count_when_no_matches():
    """A zero count from the persistence layer propagates unchanged."""
    repository = AsyncMock()
    repository.delete_by_metadata = AsyncMock(return_value=0)
    domain = DomainMemoryRepository(repository=repository)

    deleted = await domain.delete_by_metadata(
        namespace="documents",
        key="document_id",
        value="doc-x",
    )

    assert deleted == 0
    # org_id defaults to None and is still passed through.
    repository.delete_by_metadata.assert_awaited_once_with(
        namespace="documents", key="document_id", value="doc-x", org_id=None
    )



class StubEmbeddings:
    """Records embed_batch calls; returns fixed vectors."""

    def __init__(self):
        self.batch_calls: list[list[str]] = []

    async def embed(self, text: str) -> list[float]:
        return [0.0, 0.1]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls.append(list(texts))
        return [[0.0, 0.1] for _ in texts]


@pytest.mark.asyncio
async def test_store_batch_uses_single_embed_batch_call():
    from draftly.memory.models.document import Document

    repository = AsyncMock()
    repository.create_batch = AsyncMock(return_value=[{"id": "ok1"}, {"id": "ok2"}])
    embeddings = StubEmbeddings()
    domain = DomainMemoryRepository(
        repository=repository, embeddings=cast(EmbeddingService, embeddings)
    )

    items = [
        Document(namespace="documents", content="chunk one"),
        Document(namespace="documents", content="chunk two"),
    ]
    results = await domain.store_batch(items)

    assert len(results) == 2
    assert embeddings.batch_calls == [["chunk one", "chunk two"]]
    assert repository.create_batch.await_count == 1

@pytest.mark.asyncio
async def test_store_delete_by_metadata_bulk():
    client = AsyncMock(spec=DatabaseClient)
    client.transaction = MagicMock()
    conn = AsyncMock()
    client.transaction.return_value.__aenter__.return_value = conn
    client.fetch_one = AsyncMock(return_value={"deleted": 3})

    store = DatabaseMemoryStore(client=client)
    deleted = await store.delete_by_metadata_bulk(
        namespace="documents",
        key="document_id",
        value="doc-1",
        org_id="org-123",
    )

    assert deleted == 3
    client.fetch_one.assert_awaited_once()
    sql = client.fetch_one.call_args[0][0]
    assert "DELETE FROM memory_items" in sql
    assert "metadata->>$3 = $4" in sql


@pytest.mark.asyncio
async def test_delete_by_metadata_bulk_counts_via_cte_not_returning_aggregate():
    """Postgres forbids aggregates in RETURNING.

    A real DB rejects `DELETE ... RETURNING count(*)` with
    "aggregate functions are not allowed in RETURNING". The deleted
    count must come from a CTE over the de-returned rows, and the
    cascade (migration 003) handles memory_embeddings.
    """
    client = AsyncMock(spec=DatabaseClient)
    client.transaction = MagicMock()
    client.fetch_one = AsyncMock(return_value={"deleted": 3})

    store = DatabaseMemoryStore(client=client)
    deleted = await store.delete_by_metadata_bulk(
        namespace="documents",
        key="document_id",
        value="doc-1",
        org_id="org-123",
    )

    assert deleted == 3
    client.fetch_one.assert_awaited_once()
    sql = client.fetch_one.call_args[0][0]
    assert "RETURNING count" not in sql
    assert "RETURNING id" in sql
    assert sql.lstrip().startswith("WITH")
    assert "metadata->>$3 = $4" in sql

@pytest.mark.asyncio
async def test_store_insert_batch():
    client = AsyncMock(spec=DatabaseClient)
    client.transaction = MagicMock()
    conn = AsyncMock()
    client.transaction.return_value.__aenter__.return_value = conn
    client.fetch_one_conn = AsyncMock(
        side_effect=[
            {
                "id": "mem-1",
                "org_id": "o-1",
                "namespace": "n",
                "memory_type": "t",
                "content": "c",
                "summary": "",
                "status": "active",
                "importance": 1.0,
                "confidence": 1.0,
                "version": 1,
                "access_count": 0,
                "last_accessed_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "mem-2",
                "org_id": "o-1",
                "namespace": "n",
                "memory_type": "t",
                "content": "c",
                "summary": "",
                "status": "active",
                "importance": 1.0,
                "confidence": 1.0,
                "version": 1,
                "access_count": 0,
                "last_accessed_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]
    )

    store = DatabaseMemoryStore(client=client)
    items = [
        {
            "org_id": "o-1",
            "namespace": "n",
            "memory_type": "t",
            "content": "c1",
            "importance": 1.0,
            "confidence": 1.0,
            "metadata": {},
            "embedding": [0.1],
        },
        {
            "org_id": "o-1",
            "namespace": "n",
            "memory_type": "t",
            "content": "c2",
            "importance": 1.0,
            "confidence": 1.0,
            "metadata": {},
            "embedding": [0.2],
        },
    ]
    results = await store.insert_batch(items=items)

    assert len(results) == 2
    assert client.fetch_one_conn.await_count == 2
    assert client.execute_conn.await_count == 2

