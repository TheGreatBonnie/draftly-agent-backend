from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
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


def _encode_case_cursor(created_at: datetime, row_id: UUID | str) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(row_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_case_cursor(cursor: str) -> tuple[datetime, UUID]:
    padded = cursor + ("=" * (-len(cursor) % 4))
    payload = json.loads(urlsafe_b64decode(padded).decode("utf-8"))
    return datetime.fromisoformat(payload["created_at"]), UUID(payload["id"])


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
        target_id: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions = ["org_id = $1", "case_id IS NULL"]
        args: list[Any] = [org_id]
        if evaluation_type:
            args.append(evaluation_type)
            conditions.append(f"evaluation_type = ${len(args)}")
        if target_id:
            args.append(_normalize_uuid(target_id))
            conditions.append(f"target_id = ${len(args)}")
        args.append(limit)
        limit_placeholder = len(args)
        rows = await self.client.fetch_all(
            f"""
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
                WHERE {' AND '.join(conditions)}
                ORDER BY started_at DESC
                LIMIT ${limit_placeholder}
                """,
            *args,
        )

        return [self._to_dict(row) for row in rows]

    async def insert_case_results(
        self,
        *,
        org_id: str,
        evaluation_id: str,
        run_id: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Upsert granular metric rows atomically for a completed evaluation."""
        if not results:
            return []

        persisted: list[dict[str, Any]] = []
        async with self.client.transaction() as conn:
            for result in results:
                evidence = result.get("evidence")
                evidence_json = json.dumps(evidence if evidence is not None else [])
                row = await self.client.fetch_one_conn(
                    conn,
                    """
                    INSERT INTO evaluation_case_results (
                        org_id, evaluation_id, run_id, dataset, case_id, metric,
                        threshold, score, passed, reason, input, expected_output,
                        actual_output, evidence, trace_id, duration_ms
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                            $13, $14::JSONB, $15, $16)
                    ON CONFLICT (evaluation_id, dataset, case_id, metric)
                    DO UPDATE SET
                        threshold = EXCLUDED.threshold,
                        score = EXCLUDED.score,
                        passed = EXCLUDED.passed,
                        reason = EXCLUDED.reason,
                        input = EXCLUDED.input,
                        expected_output = EXCLUDED.expected_output,
                        actual_output = EXCLUDED.actual_output,
                        evidence = EXCLUDED.evidence,
                        trace_id = EXCLUDED.trace_id,
                        duration_ms = EXCLUDED.duration_ms
                    RETURNING id, org_id, evaluation_id, run_id, dataset, case_id,
                              metric, threshold, score, passed, reason, input,
                              expected_output, actual_output, evidence, trace_id,
                              duration_ms, created_at
                    """,
                    org_id,
                    _normalize_uuid(evaluation_id),
                    run_id,
                    str(result.get("dataset") or ""),
                    str(result.get("case_id") or result.get("case") or ""),
                    str(result.get("metric") or ""),
                    result.get("threshold"),
                    result.get("score"),
                    bool(result.get("passed", result.get("test_pass", False))),
                    str(result.get("reason") or ""),
                    result.get("input"),
                    result.get("expected_output"),
                    result.get("actual_output"),
                    evidence_json,
                    result.get("trace_id"),
                    result.get("duration_ms"),
                )
                persisted.append(self._case_result_to_dict(row))
        return persisted

    async def get_by_run_id(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            """
            SELECT id, org_id, evaluation_type, run_id, case_id, target_type,
                   target_id, score, passed, status, metrics, failures, trace_id,
                   started_at, completed_at
            FROM evaluations
            WHERE org_id = $1 AND run_id = $2 AND case_id IS NULL
            ORDER BY started_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            org_id,
            run_id,
        )
        return self._to_dict(row) if row else None

    async def list_case_results(
        self,
        *,
        org_id: str,
        run_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        args: list[Any] = [org_id, run_id]
        conditions = ["org_id = $1", "run_id = $2"]
        if cursor:
            created_at, row_id = _decode_case_cursor(cursor)
            args.extend([created_at, row_id])
            conditions.append("(created_at, id) < ($3, $4)")
        args.append(limit + 1)
        rows = await self.client.fetch_all(
            f"""
            SELECT id, org_id, evaluation_id, run_id, dataset, case_id, metric,
                   threshold, score, passed, reason, input, expected_output,
                   actual_output, evidence, trace_id, duration_ms, created_at
            FROM evaluation_case_results
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(args)}
            """,
            *args,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            next_cursor = _encode_case_cursor(page[-1][-1], page[-1][0])
        return [self._case_result_to_dict(row) for row in page], next_cursor

    async def list_runs(
        self,
        *,
        org_id: str,
        evaluation_type: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        conditions = ["org_id = $1", "case_id IS NULL"]
        args: list[Any] = [org_id]
        if evaluation_type:
            args.append(evaluation_type)
            conditions.append(f"evaluation_type = ${len(args)}")
        if cursor:
            started_at, row_id = _decode_case_cursor(cursor)
            args.extend([started_at, row_id])
            conditions.append(
                f"(COALESCE(started_at, created_at), id) < "
                f"(${len(args) - 1}, ${len(args)})"
            )
        args.append(limit + 1)
        rows = await self.client.fetch_all(
            f"""
            SELECT id, org_id, evaluation_type, run_id, case_id, target_type,
                   target_id, score, passed, status, metrics, failures, trace_id,
                   started_at, completed_at, created_at,
                   COUNT(*) OVER() AS total_count
            FROM evaluations
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(started_at, created_at) DESC, id DESC
            LIMIT ${len(args)}
            """,
            *args,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        total = int(
            page[0].get("total_count", len(page))
            if page and isinstance(page[0], dict)
            else len(page)
        )
        next_cursor = None
        if has_more and page:
            first = page[-1]
            if isinstance(first, dict):
                sort_value = first.get("started_at") or first.get("created_at")
                next_cursor = _encode_case_cursor(sort_value, first["id"])
        return [self._to_dict(row) for row in page], total, next_cursor

    async def aggregate_summary(
        self,
        *,
        org_id: str,
        since: datetime,
        days: int,
    ) -> dict[str, Any]:
        rows = await self.client.fetch_all(
            """
            SELECT started_at, score, passed, status, metrics
            FROM evaluations
            WHERE org_id = $1 AND case_id IS NULL AND started_at >= $2
            ORDER BY started_at ASC
            """,
            org_id,
            since,
        )
        completed = [
            row for row in rows
            if str(row.get("status", "")).lower() not in {"queued", "running"}
        ]
        scores = [float(row["score"]) for row in completed if row.get("score") is not None]
        passed_runs = sum(
            1
            for row in completed
            if bool(row.get("passed")) or row.get("status") == "passed"
        )
        trend: dict[str, list[float]] = {}
        metrics: dict[str, list[float]] = {}
        total_cases = 0
        for row in completed:
            started = row.get("started_at")
            if started:
                if isinstance(started, str):
                    started = datetime.fromisoformat(started.replace("Z", "+00:00"))
                trend.setdefault(
                    started.astimezone(UTC).date().isoformat(), []
                ).append(float(row.get("score") or 0))
            metric_rows = _decode_jsonb_column(row.get("metrics")) or {}
            total_cases += int(metric_rows.get("cases") or 0)
            for detail in metric_rows.get("granular") or []:
                if not isinstance(detail, dict) or detail.get("score") is None:
                    continue
                metrics.setdefault(
                    str(detail.get("metric") or "unknown"), []
                ).append(float(detail["score"]))
        return {
            "window_days": days,
            "average_score": sum(scores) / len(scores) if scores else None,
            "total_runs": len(completed),
            "passed_runs": passed_runs,
            "failed_runs": max(0, len(completed) - passed_runs),
            "total_cases": total_cases,
            "pass_rate": passed_runs / len(completed) if completed else None,
            "trend": [
                {"date": date, "average_score": sum(values) / len(values), "run_count": len(values)}
                for date, values in sorted(trend.items())
            ],
            "by_metric": [
                {
                    "metric": metric,
                    "average_score": sum(values) / len(values),
                    "sample_count": len(values),
                }
                for metric, values in sorted(metrics.items())
            ],
        }

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

    @staticmethod
    def _case_result_to_dict(row) -> dict[str, Any]:
        if isinstance(row, dict):
            result = dict(row)
            result["id"] = str(result["id"])
            result["evaluation_id"] = str(result["evaluation_id"])
            result["evidence"] = _decode_jsonb_column(result.get("evidence"))
            return result

        return {
            "id": str(row[0]),
            "org_id": str(row[1]),
            "evaluation_id": str(row[2]),
            "run_id": row[3],
            "dataset": row[4],
            "case_id": row[5],
            "metric": row[6],
            "threshold": row[7],
            "score": row[8],
            "passed": row[9],
            "reason": row[10],
            "input": row[11],
            "expected_output": row[12],
            "actual_output": row[13],
            "evidence": _decode_jsonb_column(row[14]),
            "trace_id": row[15],
            "duration_ms": row[16],
            "created_at": row[17],
        }
