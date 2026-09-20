"""Doc-namespace searches delegate to RagRetrieval; other namespaces keep
the legacy corpus behavior.

Plan: plans/2026-09-20-tavily-rag.md (Task 10).
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# NOTE: this import must stay first among draftly imports. The repo has a
# pre-existing draftly.memory.repository <-> draftly.persistence.repositories
# cycle that only resolves when the persistence package initializes first
# (same reason test_sync_service.py fails in isolation but passes in-suite).
import draftly.persistence.repositories  # noqa: F401
import draftly.tools.search.hybrid_search  # noqa: F401
import draftly.tools.search.keyword_search  # noqa: F401
import draftly.tools.search.semantic_search  # noqa: F401
from draftly.documentation.rag_retrieval import RagResult

# NOTE: draftly/tools/search/__init__.py re-exports the decorated tool
# functions under the submodule names, so attribute access would return the
# functions. sys.modules holds the real module objects (needed for
# patch.object and direct calls in these tests).
hybrid_mod = sys.modules["draftly.tools.search.hybrid_search"]
keyword_mod = sys.modules["draftly.tools.search.keyword_search"]
semantic_mod = sys.modules["draftly.tools.search.semantic_search"]


class StubRagRetrieval:
    instances: list[StubRagRetrieval] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.retrieve_kwargs: dict | None = None
        StubRagRetrieval.instances.append(self)

    async def retrieve(self, **kwargs):
        self.retrieve_kwargs = kwargs
        return RagResult(
            results=[
                {
                    "id": "m1",
                    "content": "indexed content",
                    "metadata": {},
                    "url": "https://docs.example.com/a",
                    "source_url": "https://docs.example.com/a",
                    "score": 0.9,
                }
            ],
            confidence=0.9,
            source="local",
        )


@pytest.fixture(autouse=True)
def _clear_stub():
    StubRagRetrieval.instances.clear()
    yield
    StubRagRetrieval.instances.clear()


@pytest.fixture(autouse=True)
def _stub_rag_dependencies():
    """Client construction needs env-bound singletons; the stubbed
    RagRetrieval never touches them, so stand-ins are sufficient here."""
    with (
        patch(
            "draftly.integrations.database.client.DatabaseClient",
            return_value=MagicMock(),
        ),
        patch(
            "draftly.memory.embeddings.EmbeddingService",
            return_value=MagicMock(),
        ),
    ):
        yield


@pytest.fixture()
def docs_scope():
    # Function-level import: draftly.memory.scope cannot be imported at
    # module top (pre-existing package cycle; resolves in full-suite runs).
    from draftly.memory.scope import (
        MemoryScope,
        reset_memory_scope,
        set_memory_scope,
    )

    token = set_memory_scope(MemoryScope(org_id="o", namespace="documents"))
    yield
    reset_memory_scope(token)


@pytest.fixture()
def solutions_scope():
    from draftly.memory.scope import (
        MemoryScope,
        reset_memory_scope,
        set_memory_scope,
    )

    token = set_memory_scope(MemoryScope(org_id="o", namespace="solutions"))
    yield
    reset_memory_scope(token)


def _patch_rag():
    return patch(
        "draftly.documentation.rag_retrieval.RagRetrieval", StubRagRetrieval
    )


@pytest.mark.asyncio
async def test_semantic_docs_namespace_delegates_to_rag(docs_scope) -> None:
    with _patch_rag():
        results = await semantic_mod.semantic_search(
            query="q", namespace="documents", limit=5
        )

    assert len(StubRagRetrieval.instances) == 1
    stub = StubRagRetrieval.instances[0]
    assert stub.retrieve_kwargs["org_id"] == "o"
    assert stub.retrieve_kwargs["query"] == "q"
    assert stub.retrieve_kwargs["limit"] == 5
    assert results[0]["source_url"] == "https://docs.example.com/a"


@pytest.mark.asyncio
async def test_keyword_docs_namespace_delegates_to_rag(docs_scope) -> None:
    with (
        _patch_rag(),
        patch(
            "draftly.integrations.database.client.DatabaseClient"
        ) as db_cls,
    ):
        results = await keyword_mod.keyword_search(
            query="q", namespace="documents", limit=5
        )

    assert len(StubRagRetrieval.instances) == 1
    # The legacy ILIKE corpus path never runs: no SQL is issued directly.
    db_cls.return_value.fetch_all.assert_not_called()
    assert results[0]["source_url"] == "https://docs.example.com/a"


@pytest.mark.asyncio
async def test_hybrid_docs_query_invokes_single_retrieve(docs_scope) -> None:
    with _patch_rag():
        results = await hybrid_mod.hybrid_search(
            query="q", namespace="documents", limit=5
        )

    assert len(StubRagRetrieval.instances) == 1
    assert len(results) == 1
    assert results[0]["source_url"] == "https://docs.example.com/a"


@pytest.mark.asyncio
async def test_semantic_non_docs_namespace_keeps_legacy_path(
    solutions_scope,
) -> None:
    searcher = MagicMock()
    searcher.search = AsyncMock(
        return_value=[{"id": "m9", "similarity": 0.5, "content": "legacy"}]
    )
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1])
    with (
        patch(
            "draftly.documentation.rag_retrieval.RagRetrieval", StubRagRetrieval
        ),
        patch(
            "draftly.integrations.database.vector_search.VectorSearch",
            return_value=searcher,
        ),
        patch.object(semantic_mod, "_get_embedding_service", return_value=embedder),
    ):
        results = await semantic_mod.semantic_search(
            query="q", namespace="solutions", limit=5
        )

    assert StubRagRetrieval.instances == []
    searcher.search.assert_awaited_once()
    assert searcher.search.await_args.kwargs["namespace"] == "solutions"
    assert results == [{"id": "m9", "similarity": 0.5, "content": "legacy"}]


@pytest.mark.asyncio
async def test_keyword_non_docs_namespace_keeps_legacy_path(
    solutions_scope,
) -> None:
    row = {
        "id": "m9",
        "org_id": "o",
        "namespace": "solutions",
        "memory_type": "fact",
        "content": "legacy",
        "summary": None,
        "status": "active",
        "importance": 0.5,
        "confidence": 0.5,
        "version": 1,
        "access_count": 0,
        "last_accessed_at": None,
        "created_at": None,
        "updated_at": None,
    }
    client = MagicMock()
    client.fetch_all = AsyncMock(return_value=[row])
    with (
        patch(
            "draftly.documentation.rag_retrieval.RagRetrieval", StubRagRetrieval
        ),
        patch(
            "draftly.integrations.database.client.DatabaseClient",
            return_value=client,
        ),
    ):
        results = await keyword_mod.keyword_search(
            query="q", namespace="solutions", limit=5
        )

    assert StubRagRetrieval.instances == []
    client.fetch_all.assert_awaited_once()
    assert "ILIKE" in client.fetch_all.await_args.args[0]
    assert results[0]["content"] == "legacy"


@pytest.mark.asyncio
async def test_hybrid_non_docs_keeps_merge_math(solutions_scope) -> None:
    sem = AsyncMock(
        return_value=[{"id": "a", "similarity": 0.5, "content": "s"}]
    )
    kw = AsyncMock(return_value=[{"id": "a", "content": "k"}])
    with (
        patch.object(hybrid_mod, "semantic_search", sem),
        patch.object(hybrid_mod, "keyword_search", kw),
    ):
        results = await hybrid_mod.hybrid_search(
            query="q", namespace="solutions", limit=5
        )

    assert StubRagRetrieval.instances == []
    sem.assert_awaited_once()
    kw.assert_awaited_once()
    # 0.7*0.5 semantic + 0.3 keyword bonus for the shared id
    assert results[0]["score"] == pytest.approx(0.35 + 0.3)
