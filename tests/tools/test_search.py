"""Unit tests for search tools with in-memory integration fakes."""

from __future__ import annotations

import pytest

from draftly.tools.search.hybrid_search import hybrid_search
from draftly.tools.search.keyword_search import keyword_search
from draftly.tools.search.semantic_search import semantic_search


class FakeVectorSearch:
    async def search(self, *, namespace, embedding, limit=10):
        assert namespace == "docs"
        assert embedding == [0.1, 0.2]
        return [
            {
                "id": "v1",
                "org_id": None,
                "namespace": namespace,
                "memory_type": "knowledge",
                "content": "semantic hit",
                "summary": "summary",
                "status": "active",
                "importance": 0.8,
                "confidence": 0.9,
                "version": 1,
                "access_count": 2,
                "last_accessed_at": None,
                "created_at": None,
                "updated_at": None,
                "similarity": 0.93,
            }
        ][:limit]


class FakeDatabaseClient:
    async def fetch_all(self, query, *args, **kwargs):
        assert "ILIKE" in query
        assert args[0] == "docs"
        assert args[1] == "%pool%"
        return [
            {
                "id": "k1",
                "org_id": None,
                "namespace": "docs",
                "memory_type": "knowledge",
                "content": "connection pool docs",
                "summary": "pool summary",
                "status": "active",
                "importance": 0.7,
                "confidence": 0.8,
                "version": 1,
                "access_count": 3,
                "last_accessed_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ][: args[2]]


@pytest.fixture
def fake_search(monkeypatch) -> None:
    monkeypatch.setattr(
        "draftly.integrations.database.vector_search.VectorSearch",
        FakeVectorSearch,
    )
    monkeypatch.setattr(
        "draftly.integrations.database.client.DatabaseClient",
        FakeDatabaseClient,
    )


@pytest.mark.asyncio
async def test_semantic_search_returns_ranked_rows(fake_search) -> None:
    rows = await semantic_search(
        query="pool",
        namespace="docs",
        embedding=[0.1, 0.2],
        limit=5,
    )
    assert rows[0]["id"] == "v1"
    assert rows[0]["similarity"] == 0.93
    assert "score" not in rows[0]


@pytest.mark.asyncio
async def test_keyword_search_queries_memory_items(fake_search) -> None:
    rows = await keyword_search(query="pool", namespace="docs", limit=5)
    assert rows[0]["id"] == "k1"
    assert rows[0]["content"] == "connection pool docs"


@pytest.mark.asyncio
async def test_hybrid_search_merges_and_ranks(fake_search) -> None:
    rows = await hybrid_search(
        query="pool",
        namespace="docs",
        embedding=[0.1, 0.2],
        limit=10,
    )
    ids = [row["id"] for row in rows]
    assert ids == ["v1", "k1"]
    assert rows[0]["score"] >= rows[1]["score"]
    assert "score" in rows[0]


@pytest.mark.asyncio
async def test_search_tools_render_schemas(fake_search) -> None:
    for tool in (semantic_search, keyword_search, hybrid_search):
        spec = tool.tool_spec
        assert spec["name"] == tool.tool_name
        assert "inputSchema" in spec
        assert spec["inputSchema"]["json"]["properties"]
