from typing import Any
from uuid import uuid4

from draftly.integrations.database.client import DatabaseClient


class DatabaseJobsStore:
    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def insert(
        self,
        *,
        job_id: Any = None,
        org_id: str,
        name: str,
        job_type: str,
        schedule: str,
        configuration: dict[str, Any],
        status: str = "pending",
    ) -> dict[str, Any]:

        if job_id is None:
            job_id = uuid4()

        row = await self.client.fetch_one(
            """
            INSERT INTO jobs (
                id,
                org_id,
                name,
                job_type,
                schedule,
                status,
                configuration
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7::JSONB
            )
            RETURNING
                id,
                org_id,
                name,
                job_type,
                schedule,
                status,
                configuration
            """,
            job_id,
            org_id,
            name,
            job_type,
            schedule,
            status,
            configuration,
        )

        return self._to_dict(row)

    async def get(
        self,
        *,
        job_id: str,
    ) -> dict[str, Any] | None:

        row = await self.client.fetch_one(
            """
            SELECT
                id,
                org_id,
                name,
                job_type,
                schedule,
                status,
                configuration,
                last_run_at,
                next_run_at
            FROM jobs
            WHERE id = $1
            """,
            job_id,
        )

        return self._to_dict(row) if row else None

    async def update_status(
        self,
        *,
        job_id: str,
        status: str,
    ) -> dict[str, Any]:

        row = await self.client.fetch_one(
            """
            UPDATE jobs
            SET
                status = $1,
                last_run_at = now()
            WHERE id = $2
            RETURNING
                id,
                org_id,
                name,
                job_type,
                schedule,
                status,
                configuration,
                last_run_at,
                next_run_at
            """,
            status,
            job_id,
        )

        if not row:
            raise ValueError(f"Job '{job_id}' was not found.")

        return self._to_dict(row)

    async def list_active(self) -> list[dict[str, Any]]:

        rows = await self.client.fetch_all(
            """
            SELECT
                id,
                org_id,
                name,
                job_type,
                schedule,
                status,
                configuration,
                last_run_at,
                next_run_at
            FROM jobs
            WHERE status = 'active'
            """,
        )

        return [self._to_dict(row) for row in rows]

    @staticmethod
    def _to_dict(row) -> dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)

        return {
            "id": str(row[0]),
            "org_id": str(row[1]),
            "name": row[2],
            "job_type": row[3],
            "schedule": row[4],
            "status": row[5],
            "configuration": row[6],
            "last_run_at": row[7],
            "next_run_at": row[8],
        }
