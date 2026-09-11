import json
from datetime import datetime
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
                $7,
                $8::JSONB
            )
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
            job_id,
            run_id,
            org_id,
            name,
            job_type,
            schedule,
            status,
            json.dumps(configuration),
        )

        return self._to_dict(row)

    async def upsert_on_conflict(
        self,
        *,
        run_id: str,
        org_id: str,
        name: str,
        job_type: str,
        schedule: str,
        configuration: dict[str, Any],
        status: str = "pending",
    ) -> dict[str, Any] | None:
        """Idempotently insert a jobs row keyed by run_id.

        The run_id column has a UNIQUE constraint (migration 036), so a
        pre-existing row (e.g. a stale/dead initial run) is left untouched
        and no error is raised. Used to guarantee a jobs row exists on the
        resumed-onboarding path so /stream-ticket never 404s.
        """

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
                $7,
                $8::JSONB
            )
            ON CONFLICT (run_id) DO NOTHING
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
            uuid4(),
            run_id,
            org_id,
            name,
            job_type,
            schedule,
            status,
            json.dumps(configuration),
        )

        if row is None:
            return None

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

    async def get_for_org(
        self,
        *,
        job_id: str,
        org_id: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            """
            SELECT
                id, run_id, org_id, name, job_type, schedule, status,
                configuration, last_run_at, next_run_at, error, result,
                started_at, completed_at, updated_at
            FROM jobs
            WHERE run_id = $1 AND org_id = $2
            """,
            job_id,
            org_id,
        )
        return self._to_dict(row) if row else None

    async def update_status(
        self,
        *,
        job_id: str,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        row = await self.client.fetch_one(
            """
            UPDATE jobs
            SET
                status = $1,
                started_at = CASE
                    WHEN $1 = 'running' AND started_at IS NULL THEN now()
                    ELSE started_at
                END,
                completed_at = CASE
                    WHEN $1 IN ('completed', 'failed', 'cancelled') THEN now()
                    ELSE completed_at
                END,
                error = $3,
                result = $4::JSONB,
                last_run_at = now(),
                updated_at = now()
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
            error,
            json.dumps(result) if result is not None else None,
        )

        if not row:
            raise ValueError(f"Job '{job_id}' was not found.")

        return self._to_dict(row)

    async def list_active(self, org_id: str | None = None) -> list[dict[str, Any]]:

        query = """
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
        """
        args: tuple[Any, ...] = ()
        if org_id is not None:
            query += " AND org_id = $1"
            args = (org_id,)

        rows = await self.client.fetch_all(
            query,
            *args,
        )

        return [self._to_dict(row) for row in rows]

    async def list_stuck(
        self,
        *,
        started_before: datetime,
        status: str = "running",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Rows still in a non-terminal status whose start predates the cutoff.

        Used by the stale-run recovery sweep to find runs a worker died on
        (the runner never ran ``_finish_result``, so the row stayed in-flight).
        """
        rows = await self.client.fetch_all(
            """
            SELECT run_id, org_id, started_at
            FROM jobs
            WHERE status = $2
              AND started_at IS NOT NULL
              AND started_at < $1
            ORDER BY started_at ASC
            LIMIT $3
            """,
            started_before,
            status,
            limit,
        )

        return [
            {
                "run_id": str(row["run_id"]),
                "org_id": str(row.get("org_id") or ""),
                "started_at": row.get("started_at"),
            }
            for row in rows
        ]

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
