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
        run_id: str | None = None,
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
                run_id,
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
                run_id,
                org_id,
                name,
                job_type,
                schedule,
                status,
                configuration
            """,
            job_id,
            run_id,
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
                run_id,
                org_id,
                name,
                job_type,
                schedule,
                status,
                configuration,
                last_run_at,
                next_run_at
            FROM jobs
            WHERE run_id = $1
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
            WHERE run_id = $2
            RETURNING
                id,
                run_id,
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
                run_id,
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
            "run_id": str(row[1]),
            "org_id": str(row[2]),
            "name": row[3],
            "job_type": row[4],
            "schedule": row[5],
            "status": row[6],
            "configuration": row[7],
            "last_run_at": row[8],
            "next_run_at": row[9],
        }
