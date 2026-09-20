"""Keyword search tool over the memory store."""

from __future__ import annotations

import asyncio
import time

import structlog
from strands.tools import tool

from draftly.tools._guard import require_nonempty

logger = structlog.get_logger(__name__)

# Guard against a silent DB/network hang burning the graph's node_timeout.
SEARCH_TIMEOUT_SECONDS = 30.0

_COLUMNS = """
    id, org_id, namespace, memory_type, content, summary, status,
    importance, confidence, version, access_count, last_accessed_at,
    created_at, updated_at
"""


def _row_to_dict(row) -> dict:
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]) if row["org_id"] else None,
        "namespace": row["namespace"],
        "memory_type": row["memory_type"],
        "content": row["content"],
        "summary": row["summary"],
        "status": row["status"],
        "importance": row["importance"],
        "confidence": row["confidence"],
        "version": row["version"],
        "access_count": row["access_count"],
        "last_accessed_at": row["last_accessed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@tool
async def keyword_search(
    query: str,
    namespace: str,
    limit: int = 10,
) -> list[dict]:
    """Search memory items by keyword in a namespace."""
    require_nonempty(query, "query", "keyword_search")
    require_nonempty(namespace, "namespace", "keyword_search")

    from draftly.integrations.database.client import DatabaseClient
    from draftly.memory.scope import current_memory_scope

    scope = current_memory_scope()
    resolved_namespace = scope.namespace if scope and scope.namespace else namespace
    org_id = scope.org_id if scope else None

    from draftly.tools.search._rag import is_documents_namespace, rag_search_results

    if is_documents_namespace(scope, namespace):
        logger.debug(
            "keyword_search_rag_delegate",
            namespace=resolved_namespace,
            org_id=org_id,
            limit=limit,
        )
        return await rag_search_results(query=query, org_id=org_id, limit=limit)

    client = DatabaseClient()
    pattern = f"%{query}%"
    org_clause = "AND org_id = $4" if org_id else ""
    args: list = [resolved_namespace, pattern, limit]
    if org_id:
        args.append(org_id)
    start = time.perf_counter()
    try:
        rows = await asyncio.wait_for(
            client.fetch_all(
                f"""
                SELECT {_COLUMNS}
                FROM memory_items
                WHERE namespace = $1
                  AND (content ILIKE $2 OR summary ILIKE $2)
                  {org_clause}
                ORDER BY importance DESC, updated_at DESC
                LIMIT $3
                """,
                *args,
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        logger.debug(
            "keyword_search_done",
            namespace=resolved_namespace,
            org_id=org_id,
            scope_active=scope is not None,
            limit=limit,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            hits=len(rows),
        )
        return [_row_to_dict(row) for row in rows]
    except TimeoutError:
        logger.error(
            "keyword_search_timeout",
            namespace=resolved_namespace,
            timeout_seconds=SEARCH_TIMEOUT_SECONDS,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"keyword search exceeded {SEARCH_TIMEOUT_SECONDS}s (offline or stalled DB)",
        )
        raise
    except Exception:
        logger.exception(
            "keyword_search_error",
            namespace=resolved_namespace,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        raise
