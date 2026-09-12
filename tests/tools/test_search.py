"""Unit tests for search tools with in-memory integration fakes."""

from __future__ import annotations

import importlib

import pytest

from draftly.tools._guard import EmptyToolInputError
from draftly.tools.search.hybrid_search import hybrid_search
from draftly.tools.search.keyword_search import keyword_search
from draftly.tools.search.semantic_search import semantic_search

semantic_search_mod = importlib.import_module("draftly.tools.search.semantic_search")


class RecordingEmbedder:
    """Records query strings; returns a fixed vector for the DB fake to assert."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.vector = [0.1, 0.2]

    async def embed(self, text: str) -> list[float]:
        self.queries.append(text)
        return self.vector


class FakeVectorSearch:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search(self, *, namespace, embedding, limit=10, org_id=None):
        self.calls.append(
            {"namespace": namespace, "embedding": embedding, "limit": limit, "org_id": org_id}
        )
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


class RecordingVectorSearch(FakeVectorSearch):
    """Records calls without asserting namespace, for scoped-run assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {
                "id": "v1",
                "org_id": kwargs.get("org_id"),
                "namespace": kwargs["namespace"],
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
        ][: kwargs.get("limit", 10)]


class FakeDatabaseClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def fetch_all(self, query, *args, **kwargs):
        self.calls.append({"query": query, "args": list(args)})
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


class ScopedFakeDatabaseClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def fetch_all(self, query, *args, **kwargs):
        self.calls.append({"query": query, "args": list(args)})
        return [
            {
                "id": "k1",
                "org_id": args[3] if len(args) > 3 else None,
                "namespace": args[0],
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
def fake_search(monkeypatch) -> RecordingEmbedder:
    monkeypatch.setattr(
        "draftly.integrations.database.vector_search.VectorSearch",
        FakeVectorSearch,
    )
    monkeypatch.setattr(
        "draftly.integrations.database.client.DatabaseClient",
        FakeDatabaseClient,
    )
    recording = RecordingEmbedder()
    monkeypatch.setattr(semantic_search_mod, "_embedding_service", recording, raising=False)
    return recording


@pytest.fixture
def scoped_search(monkeypatch) -> dict[str, RecordingEmbedder]:
    """Installs recording fakes so scoped runs can assert org/namespace wiring."""
    vector = RecordingVectorSearch()
    db = ScopedFakeDatabaseClient()
    monkeypatch.setattr(
        "draftly.integrations.database.vector_search.VectorSearch",
        lambda *a, **kw: vector,
    )
    monkeypatch.setattr(
        "draftly.integrations.database.client.DatabaseClient",
        lambda *a, **kw: db,
    )
    recording = RecordingEmbedder()
    monkeypatch.setattr(semantic_search_mod, "_embedding_service", recording, raising=False)
    return {"vector": vector, "db": db}


@pytest.mark.asyncio
async def test_semantic_search_embeds_query_server_side(fake_search) -> None:
    rows = await semantic_search(query="pool", namespace="docs", limit=5)
    assert fake_search.queries == ["pool"]
    assert rows[0]["id"] == "v1"


@pytest.mark.asyncio
async def test_semantic_search_returns_ranked_rows(fake_search) -> None:
    rows = await semantic_search(query="pool", namespace="docs", limit=5)
    assert rows[0]["similarity"] == 0.93
    assert "score" not in rows[0]


@pytest.mark.asyncio
async def test_keyword_search_queries_memory_items(fake_search) -> None:
    rows = await keyword_search(query="pool", namespace="docs", limit=5)
    assert rows[0]["id"] == "k1"
    assert rows[0]["content"] == "connection pool docs"


@pytest.mark.asyncio
async def test_hybrid_search_merges_and_ranks(fake_search) -> None:
    rows = await hybrid_search(query="pool", namespace="docs", limit=10)
    ids = [row["id"] for row in rows]
    assert ids == ["v1", "k1"]
    assert rows[0]["score"] >= rows[1]["score"]
    assert "score" in rows[0]


@pytest.mark.asyncio
async def test_hybrid_search_embeds_query_once(fake_search) -> None:
    await hybrid_search(query="pool", namespace="docs", limit=10)
    assert fake_search.queries == ["pool"]


@pytest.mark.asyncio
async def test_search_tools_render_schemas(fake_search) -> None:
    for tool in (semantic_search, keyword_search, hybrid_search):
        spec = tool.tool_spec
        assert spec["name"] == tool.tool_name
        props = spec["inputSchema"]["json"]["properties"]
        assert props
        assert "embedding" not in props, f"{tool.tool_name} must not require raw embeddings"


class TestSearchEmptyInputGuards:
    """Empty query/namespace must raise, not silently query the DB."""

    @pytest.mark.asyncio
    async def test_keyword_search_rejects_empty_query(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await keyword_search(query="", namespace="docs")

    @pytest.mark.asyncio
    async def test_keyword_search_rejects_empty_namespace(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await keyword_search(query="pool", namespace="")

    @pytest.mark.asyncio
    async def test_semantic_search_rejects_empty_query(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await semantic_search(query="", namespace="docs")

    @pytest.mark.asyncio
    async def test_semantic_search_rejects_empty_namespace(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await semantic_search(query="pool", namespace="")


class TestSearchRunMemoryScope:
    """With an active run memory scope, search tools must resolve the org's
    canonical documents namespace and constrain results to the org even when
    the LLM guesses a wrong namespace for the run."""

    @pytest.fixture(autouse=True)
    def _scope(self, request):
        import draftly.feedback  # noqa: F401  (import-order guard)
        from draftly.memory.scope import MemoryScope, reset_memory_scope, set_memory_scope

        token = set_memory_scope(MemoryScope(org_id="org-1", namespace="documents"))
        request.addfinalizer(lambda: reset_memory_scope(token))

    @pytest.mark.asyncio
    async def test_semantic_search_uses_scope_namespace_and_org(self, scoped_search) -> None:
        rows = await semantic_search(query="pool", namespace="authly", limit=5)
        assert [r["id"] for r in rows] == ["v1"]
        call = scoped_search["vector"].calls[0]
        assert call["namespace"] == "documents"
        assert call["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_keyword_search_scopes_namespace_and_org(self, scoped_search) -> None:
        rows = await keyword_search(query="pool", namespace="authly", limit=5)
        assert [r["id"] for r in rows] == ["k1"]
        call = scoped_search["db"].calls[0]
        assert call["args"][0] == "documents"
        assert "org_id" in call["query"]
        assert "org-1" in call["args"]

    @pytest.mark.asyncio
    async def test_hybrid_search_scopes_namespace_and_org(self, scoped_search) -> None:
        rows = await hybrid_search(query="pool", namespace="authly", limit=10)
        assert [r["id"] for r in rows] == ["v1", "k1"]
        call = scoped_search["vector"].calls[0]
        assert call["namespace"] == "documents"
        assert call["org_id"] == "org-1"
        kw_call = scoped_search["db"].calls[0]
        assert kw_call["args"][0] == "documents"
        assert "org-1" in kw_call["args"]
