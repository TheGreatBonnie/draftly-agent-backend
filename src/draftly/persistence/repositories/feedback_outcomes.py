"""Persistence for evaluation, review, and resolution feedback outcomes."""

from __future__ import annotations

import json
from typing import Any

from draftly.integrations.database.client import DatabaseClient


class FeedbackOutcomeRepository:
    """Store organization-scoped outcomes exactly once per source."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    async def save_outcome(
        self,
        org_id: str,
        source_type: str,
        source_id: str,
        outcome: dict[str, Any],
    ) -> str:
        row = await self.database.fetch_one(
            """
            INSERT INTO feedback_outcomes (org_id, source_type, source_id, outcome)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (org_id, source_type, source_id)
            DO UPDATE SET outcome = EXCLUDED.outcome, updated_at = now()
            RETURNING outcome_id::text
            """,
            org_id,
            source_type,
            source_id,
            json.dumps(outcome),
        )
        if row is None:
            raise RuntimeError("feedback outcome missing after upsert")
        return str(row["outcome_id"])

    async def list_unresolved(self, org_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self.database.fetch_all(
            """
            SELECT outcome_id::text, org_id, source_type, source_id,
                   outcome, created_at, updated_at
            FROM feedback_outcomes
            WHERE org_id = $1 AND resolved_at IS NULL
            ORDER BY created_at ASC
            LIMIT $2
            """,
            org_id,
            limit,
        )
        return [dict(row) for row in rows]

    async def mark_resolved(self, org_id: str, source_type: str, source_id: str) -> None:
        await self.database.execute(
            """
            UPDATE feedback_outcomes
            SET resolved_at = now(), updated_at = now()
            WHERE org_id = $1 AND source_type = $2 AND source_id = $3
            """,
            org_id,
            source_type,
            source_id,
        )
