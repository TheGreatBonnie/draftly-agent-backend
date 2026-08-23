"""Unit tests for memory deletion scoped to a metadata value."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from draftly.memory.embeddings import EmbeddingService
from draftly.memory.repository import DomainMemoryRepository


@pytest.mark.asyncio
async def test_delete_by_metadata_removes_only_matching_items():
    repository = AsyncMock()

    async def fake_list(namespace: str):
        assert namespace == "documents"
        return [
            {"id": "mem-1", "metadata": {"document_id": "doc-1"}},
            {"id": "mem-2", "metadata": {"document_id": "doc-1"}},
            {"id": "mem-3", "metadata": {"document_id": "doc-2"}},
            {"id": "mem-4", "metadata": {}},
        ]

    repository.list_namespace = AsyncMock(side_effect=fake_list)
    repository.delete = AsyncMock(return_value=True)
    domain = DomainMemoryRepository(repository=repository)

    deleted = await domain.delete_by_metadata(
        namespace="documents",
        key="document_id",
        value="doc-1",
    )

    assert deleted == 2
    deleted_ids = {call.args[0] for call in repository.delete.await_args_list}
    assert deleted_ids == {"mem-1", "mem-2"}


@pytest.mark.asyncio
async def test_delete_by_metadata_returns_zero_when_no_matches():
    repository = AsyncMock()
    repository.list_namespace = AsyncMock(return_value=[])
    domain = DomainMemoryRepository(repository=repository)

    deleted = await domain.delete_by_metadata(
        namespace="documents",
        key="document_id",
        value="doc-x",
    )

    assert deleted == 0
    repository.delete.assert_not_called()


class StubEmbeddings:
    """Records embed_batch calls; returns fixed vectors."""

    def __init__(self):
        self.batch_calls: list[list[str]] = []

    def embed(self, text: str) -> list[float]:
        return [0.0, 0.1]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls.append(list(texts))
        return [[0.0, 0.1] for _ in texts]


@pytest.mark.asyncio
async def test_store_batch_uses_single_embed_batch_call():
    from draftly.memory.models.document import Document

    repository = AsyncMock()
    repository.create = AsyncMock(side_effect=lambda **kw: {"id": "ok"})
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
    assert repository.create.await_count == 2
