"""Database stores for routing decisions and model performance."""

from __future__ import annotations

import json
from typing import Any

from draftly.integrations.database.client import DatabaseClient


class DatabaseRoutingStore:
    def __init__(self, client: DatabaseClient | None = None) -> None:
        self.client = client or DatabaseClient()

    async def record_decision(self, row: dict[str, Any]) -> None:
        await self.client.execute(
            """
            INSERT INTO routing_decisions (
                request_id, organization_id, task_type, selected_model, provider,
                score, candidates_considered, profile, reason_codes, fallback_chain,
                estimated_cost, estimated_latency_ms, actual_cost, latency_ms,
                success, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
            """,
            row["request_id"],
            row.get("organization_id"),
            row["task_type"],
            row["selected_model"],
            row["provider"],
            row["score"],
            row["candidates_considered"],
            row["profile"],
            json.dumps(list(row.get("reason_codes") or [])),
            json.dumps(list(row.get("fallback_chain") or [])),
            row.get("estimated_cost"),
            row.get("estimated_latency_ms"),
            row.get("actual_cost"),
            row.get("latency_ms"),
            row.get("success"),
            json.dumps(row.get("metadata") or {}),
        )

    async def get_recent_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            "SELECT * FROM routing_decisions ORDER BY created_at DESC LIMIT $1", limit
        )
        return [dict(row) for row in rows]


class DatabasePerformanceStore:
    def __init__(self, client: DatabaseClient | None = None) -> None:
        self.client = client or DatabaseClient()

    async def upsert_performance(self, row: dict[str, Any]) -> None:
        await self.client.execute(
            """
            INSERT INTO model_performance (
                model_name, task_type, sample_count, mean_latency_ms,
                variance_latency_ms, success_rate, p50_latency_ms,
                p95_latency_ms, quality_ema, approval_rate, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            ON CONFLICT (model_name, task_type) DO UPDATE SET
                sample_count = EXCLUDED.sample_count,
                mean_latency_ms = EXCLUDED.mean_latency_ms,
                variance_latency_ms = EXCLUDED.variance_latency_ms,
                success_rate = EXCLUDED.success_rate,
                p50_latency_ms = EXCLUDED.p50_latency_ms,
                p95_latency_ms = EXCLUDED.p95_latency_ms,
                quality_ema = EXCLUDED.quality_ema,
                approval_rate = COALESCE(EXCLUDED.approval_rate, model_performance.approval_rate),
                updated_at = NOW()
            """,
            row["model_name"],
            row["task_type"],
            row["sample_count"],
            row["mean_latency_ms"],
            row["variance_latency_ms"],
            row["success_rate"],
            row["p50_latency_ms"],
            row["p95_latency_ms"],
            row.get("quality_ema"),
            row.get("approval_rate"),
        )

    async def get_performance(self, task_type: str, model_name: str) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            "SELECT * FROM model_performance WHERE task_type = $1 AND model_name = $2",
            task_type,
            model_name,
        )
        return dict(row) if row else None

    async def get_all(self) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all("SELECT * FROM model_performance")
        return [dict(row) for row in rows]
