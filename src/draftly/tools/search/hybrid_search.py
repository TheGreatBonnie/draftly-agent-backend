"""Hybrid (semantic + keyword) search tool over the memory store."""

from __future__ import annotations

from collections.abc import Sequence

from strands.tools import tool

from draftly.tools.search.keyword_search import keyword_search
from draftly.tools.search.semantic_search import semantic_search

_SEMANTIC_WEIGHT = 0.7


@tool
async def hybrid_search(
    query: str,
    namespace: str,
    embedding: Sequence[float],
    limit: int = 10,
) -> list[dict]:
    """Combine semantic and keyword search, merging results by id."""
    semantic = await semantic_search(
        query=query,
        namespace=namespace,
        embedding=embedding,
        limit=limit,
    )
    keyword = await keyword_search(query=query, namespace=namespace, limit=limit)

    merged: dict[str, dict] = {}
    for item in semantic:
        item = dict(item)
        similarity = float(item.pop("similarity", 0.0))
        item["score"] = _SEMANTIC_WEIGHT * similarity
        merged[str(item["id"])] = item
    for item in keyword:
        item = dict(item)
        key = str(item["id"])
        if key in merged:
            merged[key]["score"] = merged[key].get("score", 0.0) + (
                1.0 - _SEMANTIC_WEIGHT
            )
        else:
            item["score"] = 1.0 - _SEMANTIC_WEIGHT
            merged[key] = item

    ranked = sorted(
        merged.values(),
        key=lambda item: item["score"],
        reverse=True,
    )
    return ranked[:limit]
