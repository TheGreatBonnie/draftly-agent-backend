"""Semantic vector search tool over the memory store."""

from __future__ import annotations

import asyncio
import time

import structlog
from strands.tools import tool

from draftly.tools._guard import require_nonempty

logger = structlog.get_logger(__name__)

# Lazy embedder: builds an EmbeddingService on first use (itself lazily building
# an EmbeddingRouter from env config), so the agent never has to supply a raw
# embedding vector — the LLM cannot produce one and would only fabricate a
# dimension-mismatched guess. Kept behind a getter to avoid importing the whole
# memory package at tool-import time (circular-import hazard).
_embedding_service: object | None = None


def _get_embedding_service() -> object:
    global _embedding_service
    if _embedding_service is None:
        from draftly.memory.embeddings import EmbeddingService

        _embedding_service = EmbeddingService()
    return _embedding_service


# Guard against a silent network/DB hang that would otherwise burn the graph's
# whole node_timeout budget. Anything slower than this is almost certainly
# stuck (offline DB, stalled pool, unreachable Neon) and should fail loudly.
SEARCH_TIMEOUT_SECONDS = 30.0


@tool
async def semantic_search(
    query: str,
    namespace: str,
    limit: int = 10,
) -> list[dict]:
    """Search memory items by embedding similarity in a namespace."""
    require_nonempty(query, "query", "semantic_search")
    require_nonempty(namespace, "namespace", "semantic_search")

    from draftly.integrations.database.vector_search import VectorSearch
    from draftly.memory.scope import current_memory_scope

    scope = current_memory_scope()
    resolved_namespace = scope.namespace if scope and scope.namespace else namespace
    org_id = scope.org_id if scope else None

    searcher = VectorSearch()
    start = time.perf_counter()
    try:
        embedding = await _get_embedding_service().embed(query)
        results = await asyncio.wait_for(
            searcher.search(
                namespace=resolved_namespace,
                embedding=embedding,
                limit=limit,
                org_id=org_id,
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        logger.debug(
            "semantic_search_done",
            namespace=resolved_namespace,
            org_id=org_id,
            scope_active=scope is not None,
            limit=limit,
            embedding_dims=len(embedding),
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            hits=len(results),
        )
        return results
    except TimeoutError:
        logger.error(
            "semantic_search_timeout",
            namespace=resolved_namespace,
            limit=limit,
            timeout_seconds=SEARCH_TIMEOUT_SECONDS,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"vector search exceeded {SEARCH_TIMEOUT_SECONDS}s (offline or stalled DB)",
        )
        raise
    except Exception:
        logger.exception(
            "semantic_search_error",
            namespace=resolved_namespace,
            limit=limit,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        raise
