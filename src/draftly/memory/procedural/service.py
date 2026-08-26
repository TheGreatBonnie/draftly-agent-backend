"""Procedural memory facade — learned investigation playbooks."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

ARCHIVE_CONFIDENCE = 0.3
MIN_APPLICATIONS_BEFORE_ARCHIVE = 3


class ProceduralService:
    """Store, match, and confidence-rank learned procedures."""

    def __init__(self, store: Any = None, embeddings: Any = None) -> None:
        from draftly.integrations.database.procedures_store import ProceduresStore
        from draftly.memory.embeddings import EmbeddingService

        self.store = store or ProceduresStore()
        self.embeddings = embeddings or EmbeddingService()

    async def create(
        self,
        name: str,
        pattern_description: str,
        *,
        org_id: str | None = None,
        trigger_conditions: dict | None = None,
        steps: list | None = None,
        applicability_context: str | None = None,
    ) -> dict[str, Any]:
        return await self.store.insert(
            fields={
                "org_id": org_id,
                "name": name,
                "pattern_description": pattern_description,
                "trigger_conditions": trigger_conditions or {},
                "steps": steps or [],
                "applicability_context": applicability_context,
                "confidence": 0.5,
                "embedding": await self.embeddings.embed(pattern_description),
            }
        )

    async def match(
        self,
        query: str,
        *,
        org_id: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        try:
            return await self.store.search(
                embedding=await self.embeddings.embed(query),
                org_id=org_id,
                limit=limit,
            )
        except Exception:
            logger.exception("procedure_match_failed")
            return []

    async def _apply_outcome(self, procedure_id: str, *, success: bool) -> dict[str, Any]:
        row = await self.store.get(procedure_id)
        if row is None:
            raise KeyError(f"procedure not found: {procedure_id}")
        applied = int(row["success_count"]) + int(row["failure_count"]) + 1
        if success:
            confidence = min(
                1.0, float(row["confidence"]) + (1.0 - float(row["confidence"])) * 0.25
            )
        else:
            confidence = max(0.0, float(row["confidence"]) * 0.75)
        status = row.get("status", "active")
        if applied >= MIN_APPLICATIONS_BEFORE_ARCHIVE and confidence < ARCHIVE_CONFIDENCE:
            status = "archived"
        updates: dict[str, Any] = {
            "confidence": confidence,
            "status": status,
            ("success_count" if success else "failure_count"): int(
                row["success_count" if success else "failure_count"]
            )
            + 1,
        }
        if success:
            updates["last_applied_at"] = "now()"
        return await self.store.update(procedure_id, **updates)

    async def reinforce(self, procedure_id: str) -> dict[str, Any]:
        return await self._apply_outcome(procedure_id, success=True)

    async def invalidate(self, procedure_id: str) -> dict[str, Any]:
        return await self._apply_outcome(procedure_id, success=False)
