from __future__ import annotations

from typing import Any

from draftly.integrations.database.jobs_store import DatabaseJobsStore


class JobRepositoryImpl:
    def __init__(
        self,
        store: DatabaseJobsStore | None = None,
    ) -> None:
        self.store = store or DatabaseJobsStore()

    async def get(self, *, job_id: str) -> dict[str, Any] | None:
        return await self.store.get(job_id=job_id)

    async def insert(self, **kwargs: Any) -> dict[str, Any]:
        """App-wired / /stream-ticket surface for persisting a jobs row."""
        return await self.store.insert(**kwargs)

    async def upsert_on_conflict(self, **kwargs: Any) -> dict[str, Any] | None:
        return await self.store.upsert_on_conflict(**kwargs)

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        return await self.store.insert(**kwargs)

    async def update_status(
        self,
        *,
        job_id: str,
        status: str,
    ) -> dict[str, Any]:
        return await self.store.update_status(
            job_id=job_id,
            status=status,
        )

    async def list_active(self) -> list[dict[str, Any]]:
        return await self.store.list_active()
