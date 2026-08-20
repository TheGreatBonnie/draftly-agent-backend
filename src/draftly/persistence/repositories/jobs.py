from __future__ import annotations

from typing import Any

from integrations.cockroachdb.jobs_store import CockroachJobsStore


class JobRepositoryImpl:
    def __init__(
        self,
        store: CockroachJobsStore | None = None,
    ) -> None:
        self.store = store or CockroachJobsStore()

    async def get(self, *, job_id: str) -> dict[str, Any] | None:
        return await self.store.get(job_id=job_id)

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
