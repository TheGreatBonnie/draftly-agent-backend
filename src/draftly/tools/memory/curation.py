"""Curator write tools: supersede / reinforce / archive."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from strands.tools import tool

if TYPE_CHECKING:
    from draftly.memory.service import MemoryService


def _memory_service() -> MemoryService:
    from draftly.memory.service import MemoryService

    return MemoryService()


@tool
async def supersede_memory(
    old_memory_id: str,
    new_content: str,
    namespace: str,
    org_id: str,
    evidence_json: str = "[]",
) -> str:
    """Replace an outdated memory with corrected content.

    Marks the old record superseded (kept for history but excluded from
    retrieval) and stores a new active record that references it.

    Args:
        old_memory_id: UUID of the outdated memory record.
        new_content: The corrected factual statement.
        namespace: Namespace of the old record (e.g. 'knowledge').
        org_id: Organization scope for the replacement record.
        evidence_json: JSON array of evidence paths/ids supporting the update.
    """
    result = await _memory_service().supersede(
        old_memory_id,
        new_content,
        namespace=namespace,
        org_id=org_id,
        evidence=json.loads(evidence_json),
    )
    return json.dumps(
        {
            "superseded": result is not None,
            "new_id": result.get("id") if isinstance(result, dict) else None,
        }
    )


@tool
async def reinforce_memory(memory_id: str, amount: float = 0.05) -> str:
    """Increase confidence of a corroborated memory.

    Args:
        memory_id: UUID of the confirmed memory record.
        amount: Confidence increment between 0 and 0.25.
    """
    service = _memory_service()
    current = await service.repository.get(memory_id)
    if current is None:
        return json.dumps({"ok": False, "error": "not found"})
    updated = await service.repository.update(
        memory_id,
        confidence=min(1.0, float(current.get("confidence", 0.5)) + float(amount)),
    )
    confidence = updated.get("confidence") if isinstance(updated, dict) else None
    return json.dumps({"ok": True, "confidence": confidence})


@tool
async def archive_memory(memory_id: str) -> str:
    """Soft-evict a memory: archived records stay queryable explicitly but
    never resurface in grounding retrieval.

    Args:
        memory_id: UUID of the memory record to archive.
    """
    ok = await _memory_service().repository.set_status(
        memory_id=memory_id, status="archived"
    )
    return json.dumps({"archived": bool(ok)})
