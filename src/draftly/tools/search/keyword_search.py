"""Keyword search tool over the memory store."""

from __future__ import annotations

from strands.tools import tool

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
    from draftly.integrations.database.client import DatabaseClient

    client = DatabaseClient()
    pattern = f"%{query}%"
    rows = await client.fetch_all(
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
    )
    return [_row_to_dict(row) for row in rows]
