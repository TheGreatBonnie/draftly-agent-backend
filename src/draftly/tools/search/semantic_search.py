"""Semantic vector search tool over the memory store."""

from __future__ import annotations

from collections.abc import Sequence

from strands.tools import tool


@tool
async def semantic_search(
    query: str,
    namespace: str,
    embedding: Sequence[float],
    limit: int = 10,
) -> list[dict]:
    """Search memory items by embedding similarity in a namespace."""
    from draftly.integrations.database.vector_search import VectorSearch

    searcher = VectorSearch()
    return await searcher.search(
        namespace=namespace,
        embedding=embedding,
        limit=limit,
    )
