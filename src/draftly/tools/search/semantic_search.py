"""Semantic vector search tool over the memory store."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import structlog
from strands.tools import tool

from draftly.tools._guard import require_nonempty

logger = structlog.get_logger(__name__)

# Guard against a silent network/DB hang that would otherwise burn the graph's
# whole node_timeout budget. Anything slower than this is almost certainly
# stuck (offline DB, stalled pool, unreachable Neon) and should fail loudly.
SEARCH_TIMEOUT_SECONDS = 30.0


@tool
async def semantic_search(
    query: str,
    namespace: str,
    embedding: Sequence[float],
    limit: int = 10,
) -> list[dict]:
    """Search memory items by embedding similarity in a namespace."""
    require_nonempty(query, "query", "semantic_search")
    require_nonempty(namespace, "namespace", "semantic_search")
    from draftly.integrations.database.vector_search import VectorSearch

    searcher = VectorSearch()
    start = time.perf_counter()
    try:
        results = await asyncio.wait_for(
            searcher.search(
                namespace=namespace,
                embedding=embedding,
                limit=limit,
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        logger.debug(
            "semantic_search_done",
            namespace=namespace,
            limit=limit,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            hits=len(results),
        )
        return results
    except TimeoutError:
        logger.error(
            "semantic_search_timeout",
            namespace=namespace,
            limit=limit,
            timeout_seconds=SEARCH_TIMEOUT_SECONDS,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"vector search exceeded {SEARCH_TIMEOUT_SECONDS}s (offline or stalled DB)",
        )
        raise
    except Exception:
        logger.exception(
            "semantic_search_error",
            namespace=namespace,
            limit=limit,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        raise
