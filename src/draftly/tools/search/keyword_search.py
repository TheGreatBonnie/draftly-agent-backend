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

    client = DatabaseClient()
    pattern = f"%{query}%"
    start = time.perf_counter()
    try:
        rows = await asyncio.wait_for(
            client.fetch_all(
                f"""
                SELECT {_COLUMNS}
                FROM memory_items
                WHERE namespace = $1
                  AND (content ILIKE $2 OR summary ILIKE $2)
                ORDER BY importance DESC, updated_at DESC
                LIMIT $3
                """,
                namespace,
                pattern,
                limit,
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        logger.debug(
            "keyword_search_done",
            namespace=namespace,
            limit=limit,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            hits=len(rows),
        )
        return [_row_to_dict(row) for row in rows]
    except TimeoutError:
        logger.error(
            "keyword_search_timeout",
            namespace=namespace,
            timeout_seconds=SEARCH_TIMEOUT_SECONDS,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"keyword search exceeded {SEARCH_TIMEOUT_SECONDS}s (offline or stalled DB)",
        )
        raise
    except Exception:
        logger.exception(
            "keyword_search_error",
            namespace=namespace,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        raise
