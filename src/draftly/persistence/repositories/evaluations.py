from __future__ import annotations

from datetime import datetime
from typing import Any

from draftly.integrations.database.evaluations_store import (
    DatabaseEvaluationsStore,
)


class EvaluationRepository:
    def __init__(
        self,
        store: DatabaseEvaluationsStore | None = None,
    ) -> None:
        self.store = store or DatabaseEvaluationsStore()

    async def create(
        self,
        *,
        org_id: str,
        evaluation_type: str,
        target_id: str | None,
        score: float,
        status: str,
        metrics: dict[str, Any],
        failures: list[dict[str, Any]],
        started_at: datetime,
        completed_at: datetime,
    ) -> dict[str, Any]:
        return await self.store.insert(
            org_id=org_id,
            evaluation_type=evaluation_type,
            target_id=target_id,
            score=score,
            status=status,
            metrics=metrics,
            failures=failures,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def get(
        self,
        *,
        evaluation_id: str,
    ) -> dict[str, Any] | None:
        return await self.store.get(evaluation_id=evaluation_id)

    async def search(
        self,
        *,
        org_id: str,
        evaluation_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self.store.search(
            org_id=org_id,
            evaluation_type=evaluation_type,
            limit=limit,
        )
