from __future__ import annotations

from typing import Any

from draftly.integrations.database.workflow_events_store import WorkflowEventsStore


class WorkflowEventRepositoryImpl:
    def __init__(self, store: WorkflowEventsStore | None = None) -> None:
        self.store = store or WorkflowEventsStore()

    async def append(self, envelope: dict[str, Any]) -> None:
        await self.store.append(envelope)

    async def list_after(
        self,
        run_id: str,
        *,
        seq: int,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return await self.store.list_after(run_id, seq=seq, limit=limit)
