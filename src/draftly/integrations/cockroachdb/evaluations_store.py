from datetime import datetime
from typing import Any
from uuid import uuid4

from integrations.cockroachdb.client import CockroachDBClient


class CockroachEvaluationsStore:

    def __init__(
        self,
        client: CockroachDBClient | None = None,
    ) -> None:
        self.client = client or CockroachDBClient()

    async def insert(
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

        evaluation_id = uuid4()

        row = await self.client.fetch_one(
            """
            INSERT INTO evaluations (
                id,
                org_id,
                evaluation_type,
                target_id,
                score,
                status,
                metrics,
                failures,
                started_at,
                completed_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7::JSONB,
                $8::JSONB,
                $9,
                $10
            )
            RETURNING
                id,
                org_id,
                evaluation_type,
                target_id,
                score,
                status,
                metrics,
                failures,
                started_at,
                completed_at
            """,
            evaluation_id,
            org_id,
            evaluation_type,
            target_id,
            score,
            status,
            metrics,
            failures,
            started_at,
            completed_at,
        )

        return self._to_dict(row)

    async def get(
        self,
        *,
        evaluation_id: str,
    ) -> dict[str, Any] | None:

        row = await self.client.fetch_one(
            """
            SELECT
                id,
                org_id,
                evaluation_type,
                target_id,
                score,
                status,
                metrics,
                failures,
                started_at,
                completed_at
            FROM evaluations
            WHERE id = $1
            """,
            evaluation_id,
        )

        return self._to_dict(row) if row else None

    async def search(
        self,
        *,
        org_id: str,
        evaluation_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:

        if evaluation_type:
            rows = await self.client.fetch_all(
                """
                SELECT
                    id,
                    org_id,
                    evaluation_type,
                    target_id,
                    score,
                    status,
                    metrics,
                    failures,
                    started_at,
                    completed_at
                FROM evaluations
                WHERE org_id = $1
                  AND evaluation_type = $2
                ORDER BY started_at DESC
                LIMIT $3
                """,
                org_id,
                evaluation_type,
                limit,
            )
        else:
            rows = await self.client.fetch_all(
                """
                SELECT
                    id,
                    org_id,
                    evaluation_type,
                    target_id,
                    score,
                    status,
                    metrics,
                    failures,
                    started_at,
                    completed_at
                FROM evaluations
                WHERE org_id = $1
                ORDER BY started_at DESC
                LIMIT $2
                """,
                org_id,
                limit,
            )

        return [
            self._to_dict(row)
            for row in rows
        ]

    @staticmethod
    def _to_dict(row) -> dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)

        return {
            "id": str(row[0]),
            "org_id": str(row[1]),
            "evaluation_type": row[2],
            "target_id": row[3],
            "score": row[4],
            "status": row[5],
            "metrics": row[6],
            "failures": row[7],
            "started_at": row[8],
            "completed_at": row[9],
        }
