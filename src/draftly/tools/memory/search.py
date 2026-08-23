"""Curator read tools (Strands function-based tools)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from strands.tools import tool

if TYPE_CHECKING:
    from draftly.memory.service import MemoryService


def _memory_service() -> MemoryService:
    from draftly.memory.service import MemoryService

    return MemoryService()


@tool
async def memory_search(namespace: str, query: str, limit: int = 5) -> list[dict]:
    """Search active long-term memory by semantic similarity.

    Args:
        namespace: Memory namespace to search (e.g. 'knowledge', 'solutions').
        query: Natural-language query describing the knowledge to find.
        limit: Maximum number of records to return.
    """
    service = _memory_service()
    results: list[dict[str, Any]] = await service.recall(
        namespace=namespace, query=query, limit=limit
    )
    return results


@tool
async def get_memory(memory_id: str) -> dict:
    """Fetch one memory record with its full content and metadata.

    Args:
        memory_id: UUID of the memory record.
    """
    record: dict[str, Any] | None = await _memory_service().repository.get(memory_id)
    return record if record is not None else {}
