from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from draftly.integrations.database.client import DatabaseClient


def _decode_jsonb_column(value: Any) -> Any:
    """Coerce a JSONB column value to a Python container.

    With the connection-level ``json``/``jsonb`` codec registered (see
    ``DatabaseClient``) asyncpg hands back dict/list objects directly. This
    guard is defense-in-depth for any connection/path where the column still
    arrives as a raw JSON string (asyncpg's default jsonb codec): it parses
    it so consumers never receive JSON text for semantically-JSONB fields
    (e.g. ``evaluations.metrics`` / ``evaluations.failures``).
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


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
        case_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None,
        score: float,
        passed: bool = False,
        status: str,
        metrics: dict[str, Any],
        failures: list[dict[str, Any]],
        trace_id: str | None = None,
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
                case_id,
                target_type,
                target_id,
                score,
                passed,
                status,
                metrics,
                failures,
                trace_id,
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
                $8,
                $9,
                $10,
                $11::JSONB,
                $12::JSONB,
                $13,
                $14,
                $15
            )
            RETURNING
                id,
                org_id,
                evaluation_type,
                run_id,
                case_id,
                target_type,
                target_id,
                score,
                passed,
                status,
                metrics,
                failures,
                trace_id,
                started_at,
                completed_at
            """,
            evaluation_id,
            org_id,
            evaluation_type,
            run_id,
            case_id,
            target_type,
            _normalize_uuid(target_id),
            score,
            passed,
            status,
            metrics_json,
            failures_json,
            trace_id,
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
                case_id,
                target_type,
                target_id,
                score,
                passed,
                status,
                metrics,
                failures,
                trace_id,
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
                    case_id,
                    target_type,
                    target_id,
                    score,
                    passed,
                    status,
                    metrics,
                    failures,
                    trace_id,
                    started_at,
                    completed_at
                FROM evaluations
                WHERE org_id = $1
                  AND evaluation_type = $2
                  AND case_id IS NULL
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
                    case_id,
                    target_type,
                    target_id,
                    score,
                    passed,
                    status,
                    metrics,
                    failures,
                    trace_id,
                    started_at,
                    completed_at
                FROM evaluations
                WHERE org_id = $1
                  AND case_id IS NULL
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
            result = dict(row)
            for key in ("metrics", "failures"):
                if key in result:
                    result[key] = _decode_jsonb_column(result[key])
            return result

        return {
            "id": str(row[0]),
            "org_id": str(row[1]),
            "evaluation_type": row[2],
            "run_id": row[3],
            "case_id": row[4],
            "target_type": row[5],
            "target_id": row[6],
            "score": row[7],
            "passed": row[8],
            "status": row[9],
            "metrics": _decode_jsonb_column(row[10]),
            "failures": _decode_jsonb_column(row[11]),
            "trace_id": row[12],
            "started_at": row[13],
            "completed_at": row[14],
        }
