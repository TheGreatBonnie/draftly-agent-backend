"""Search tools normalize heterogeneous LLM-supplied argument types.

The impact agents frequently emit `query`/`namespace` as a JSON array
(e.g. `{"query": ["oauth", "login"]}`) instead of a plain string. Strands
rejects the call at argument binding, the model thrashes and gives up, and the
search never runs — the observed failure behind the single-fabricated-doc
impact plan (`docs/oauth.md`). These tests pin the coercion contract:

- `str` passes through;
- `list[str]` / `tuple[str]` join on spaces;
- empty/blank (including empty lists) still raise `EmptyToolInputError`
  with the same retryable-message contract as `require_nonempty`.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# NOTE: mirror test_search_repoint.py — the persistence package must import
# first (pre-existing package cycle; resolves in full-suite runs).
import draftly.persistence.repositories  # noqa: F401
import draftly.tools.search.keyword_search  # noqa: F401
import draftly.tools.search.semantic_search  # noqa: F401
from draftly.tools._guard import EmptyToolInputError, coerce_string

keyword_mod = sys.modules["draftly.tools.search.keyword_search"]
semantic_mod = sys.modules["draftly.tools.search.semantic_search"]


@pytest.mark.parametrize("search_tool", [keyword_mod.keyword_search, semantic_mod.semantic_search])
def test_search_tool_schema_accepts_array_arguments(search_tool) -> None:
    schema = search_tool.tool_spec["inputSchema"]["json"]
    for field in ("query", "namespace"):
        allowed = schema["properties"][field]
        assert "array" in str(allowed)


# -- coerce_string -----------------------------------------------------------


def test_coerce_string_passes_str_through() -> None:
    assert coerce_string("oauth login", "query", "keyword_search") == "oauth login"


def test_coerce_string_joins_list_into_string() -> None:
    assert coerce_string(["oauth", "login"], "query", "keyword_search") == "oauth login"


def test_coerce_string_joins_tuple_and_single_item_list() -> None:
    assert coerce_string(("oauth", "login"), "query", "keyword_search") == "oauth login"
    assert coerce_string(["oauth login"], "query", "keyword_search") == "oauth login"


def test_coerce_string_skips_blank_items() -> None:
    assert coerce_string(["oauth", "", "  ", "login"], "query", "keyword_search") == (
        "oauth login"
    )


def test_coerce_string_empty_input_raises() -> None:
    for bad in ("", "   ", [], [""], [None], None):
        with pytest.raises(EmptyToolInputError, match="query"):
            coerce_string(bad, "query", "keyword_search")


# -- keyword_search ----------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_search_coerces_list_query_and_namespace() -> None:
    row = {
        "id": "m9",
        "org_id": "o",
        "namespace": "docs",
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
    with patch(
        "draftly.integrations.database.client.DatabaseClient", return_value=client
    ):
        results = await keyword_mod.keyword_search(
            query=["oauth", "login"], namespace=["docs"], limit=5
        )

    assert results == [row]
    client.fetch_all.assert_awaited_once()
    # fetch_all(query, resolved_namespace, pattern, limit)
    params = client.fetch_all.await_args.args[1:]
    assert params == ("docs", "%oauth login%", 5)


@pytest.mark.asyncio
async def test_keyword_search_empty_list_argument_raises() -> None:
    client = MagicMock()
    client.fetch_all = AsyncMock(return_value=[])
    with patch(
        "draftly.integrations.database.client.DatabaseClient", return_value=client
    ):
        with pytest.raises(EmptyToolInputError, match="query"):
            await keyword_mod.keyword_search(query=[], namespace=["docs"], limit=5)
    client.fetch_all.assert_not_called()


# -- semantic_search ---------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_search_coerces_list_query_and_namespace() -> None:
    searcher = MagicMock()
    searcher.search = AsyncMock(
        return_value=[{"id": "m9", "similarity": 0.5, "content": "legacy"}]
    )
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1])
    with (
        patch(
            "draftly.integrations.database.vector_search.VectorSearch",
            return_value=searcher,
        ),
        patch.object(semantic_mod, "_get_embedding_service", return_value=embedder),
    ):
        results = await semantic_mod.semantic_search(
            query=["oauth", "login"], namespace=["docs"], limit=5
        )

    embedder.embed.assert_awaited_once_with("oauth login")
    assert searcher.search.await_args.kwargs["namespace"] == "docs"
    assert searcher.search.await_args.kwargs["limit"] == 5
    assert results == [{"id": "m9", "similarity": 0.5, "content": "legacy"}]
