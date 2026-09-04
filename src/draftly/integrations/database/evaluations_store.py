import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from draftly.integrations.database.client import DatabaseClient


def _normalize_uuid(value: str | UUID | None) -> UUID | None:
    """Coerce an identifier to a valid UUID or None.

    ``target_id`` is a ``UUID`` column, but callers thread the prefixed
    run_id (e.g. ``evaluation-<uuid>``) into it. Strip any ``<prefix>-``
    prefix so the raw UUID parses instead of raising a Postgres DataError.
    Returns None for values that cannot be parsed as a UUID.
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    text = str(value).strip()
    candidates = [text]
    if text.count("-") == 5:
        candidates.append(text.split("-", 1)[-1])
    try:
        return UUID(text)
    except (ValueError, AttributeError):
        pass
    for candidate in candidates:
        try:
            return UUID(candidate)
        except (ValueError, AttributeError):
            continue
    return None


class DatabaseEvaluationsStore:
    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def insert(
        self,
        *,
        org_id: str,
        evaluation_type: str,
        run_id: str | None = None,
        target_id: str | None,
        score: float,
        status: str,
        metrics: dict[str, Any],
        failures: list[dict[str, Any]],
        started_at: datetime,
        completed_at: datetime,
    ) -> dict[str, Any]:

        evaluation_id = uuid4()

        # asyncpg's JSONB codec requires the parameter to be a JSON-encoded
        # str (it errors with "expected str, got dict" on Python containers),
        # so serialize before handing it to the $N::JSONB casts.
        metrics_json = json.dumps(metrics, ensure_ascii=False)
        failures_json = json.dumps(failures, ensure_ascii=False)

        row = await self.client.fetch_one(
            """
            INSERT INTO evaluations (
                id,
                org_id,
                evaluation_type,
                run_id,
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
                $7,
                $8::JSONB,
                $9::JSONB,
                $10,
                $11
            )
            RETURNING
                id,
                org_id,
                evaluation_type,
                run_id,
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
            run_id,
            _normalize_uuid(target_id),
            score,
            status,
            metrics_json,
            failures_json,
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
                run_id,
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
                    run_id,
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
                    run_id,
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

        return [self._to_dict(row) for row in rows]

    @staticmethod
    def _to_dict(row) -> dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)

        return {
            "id": str(row[0]),
            "org_id": str(row[1]),
            "evaluation_type": row[2],
            "run_id": row[3],
            "target_id": row[4],
            "score": row[5],
            "status": row[6],
            "metrics": row[7],
            "failures": row[8],
            "started_at": row[9],
            "completed_at": row[10],
        }
