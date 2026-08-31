"""Async store for the memory_candidates outbox table."""

from __future__ import annotations

import json
from typing import Any

from draftly.integrations.database.client import DatabaseClient

_INSERT_SQL = """
INSERT INTO memory_candidates (
    org_id, candidate_type, payload, source_type, source_id,
    evidence, confidence
) VALUES ($1, $2, $3::JSONB, $4, $5, $6::JSONB, $7)
RETURNING id, org_id, candidate_type, payload, source_type,
          source_id, evidence, confidence, status,
          decision_reason, created_at
"""


def _serialize_fields(fields: dict[str, Any]) -> tuple[Any, ...]:
    """Return the INSERT parameter tuple in column order, serializing JSONB
    columns and normalizing confidence as the single-row path does."""
    return (
        fields.get("org_id"),
        fields["candidate_type"],
        fields["payload"]
        if isinstance(fields["payload"], str)
        else json.dumps(fields["payload"]),
        fields.get("source_type"),
        fields.get("source_id"),
        fields["evidence"]
        if isinstance(fields["evidence"], str)
        else json.dumps(fields["evidence"]),
        float(fields.get("confidence", 0.5)),
    )


class MemoryCandidatesStore:
    def __init__(self, client: DatabaseClient | None = None) -> None:
        self.client = client or DatabaseClient()

    async def insert(self, *, fields: dict[str, Any]) -> dict[str, Any]:
        row = await self.client.fetch_one(_INSERT_SQL, *_serialize_fields(fields))
        return dict(row) if row else {}

    async def insert_batch(
        self, *, fields_list: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows = []
        async with self.client.transaction() as conn:
            for fields in fields_list:
                row = await self.client.fetch_one_conn(
                    conn, _INSERT_SQL, *_serialize_fields(fields)
                )
                rows.append(dict(row))
        return rows

    async def claim_pending(self, *, limit: int) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            """
            UPDATE memory_candidates SET status = 'processing'
            WHERE id IN (
                SELECT id FROM memory_candidates WHERE status = 'pending'
                ORDER BY created_at LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            limit,
        )
        return [dict(r) for r in rows]

    async def set_status(self, candidate_id: str, status: str, reason: str) -> None:
        await self.client.execute(
            """
            UPDATE memory_candidates
            SET status = $2, decision_reason = $3, processed_at = now()
            WHERE id = $1::UUID
            """,
            candidate_id,
            status,
            reason,
        )

    async def list_by_status(self, status: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            "SELECT * FROM memory_candidates WHERE status = $1"
            " ORDER BY created_at LIMIT $2",
            status,
            limit,
        )
        return [dict(r) for r in rows]
