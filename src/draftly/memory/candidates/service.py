"""CandidateService — curation outbox facade."""

from __future__ import annotations

import json
from typing import Any

import structlog

from draftly.memory.candidates.models import MemoryCandidate

logger = structlog.get_logger(__name__)


class CandidateService:
    def __init__(self, store: Any = None) -> None:
        from draftly.integrations.database.memory_candidates_store import (
            MemoryCandidatesStore,
        )

        self.store = store or MemoryCandidatesStore()

    async def enqueue(self, candidate: MemoryCandidate) -> dict[str, Any]:
        record = await self.store.insert(
            fields={
                "org_id": candidate.org_id,
                "candidate_type": candidate.candidate_type,
                "payload": json.dumps(candidate.payload),
                "source_type": candidate.source_type,
                "source_id": candidate.source_id,
                "evidence": json.dumps(candidate.evidence),
                "confidence": candidate.confidence,
            }
        )
        logger.debug("candidate_enqueued type=%s", candidate.candidate_type)
        return record

    async def claim_batch(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self.store.claim_pending(limit=limit)

    async def mark_applied(self, candidate_id: str, reason: str = "") -> None:
        await self.store.set_status(candidate_id, "applied", reason)

    async def mark_rejected(self, candidate_id: str, reason: str = "") -> None:
        await self.store.set_status(candidate_id, "rejected", reason)

    async def set_status_pending(self, candidate_id: str, reason: str = "") -> None:
        """Return a claimed candidate to the pending queue (retry later)."""
        await self.store.set_status(candidate_id, "pending", reason)
