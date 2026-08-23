"""Async procedures store backed by the procedures table."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from draftly.integrations.database.client import DatabaseClient
from draftly.memory.vector_utils import format_vector, normalize_vector

_PROCEDURE_COLUMNS = """id, org_id, name, pattern_description,
    trigger_conditions, steps, applicability_context, success_count,
    failure_count, confidence, status, last_applied_at, created_at,
    updated_at"""


class ProceduresStore:
    def __init__(self, client: DatabaseClient | None = None) -> None:
        self.client = client or DatabaseClient()

    async def insert(self, *, fields: dict[str, Any]) -> dict[str, Any]:
        embedding = normalize_vector(fields.pop("embedding"))
        row = await self.client.fetch_one(
            """
            INSERT INTO procedures (
                org_id, name, pattern_description, trigger_conditions,
                steps, applicability_context, confidence, embedding
            ) VALUES ($1,$2,$3,$4::JSONB,$5::JSONB,$6,$7,$8::VECTOR)
            RETURNING id, org_id, name, pattern_description,
                trigger_conditions, steps, applicability_context,
                success_count, failure_count, confidence, status,
                last_applied_at, created_at, updated_at
            """,
            fields.get("org_id"),
            fields["name"],
            fields["pattern_description"],
            json.dumps(fields.get("trigger_conditions") or {}),
            json.dumps(fields.get("steps") or []),
            fields.get("applicability_context"),
            float(fields.get("confidence", 0.5)),
            format_vector(embedding),
        )
        return dict(row) if row else {}

    async def get(self, procedure_id: str) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            f"SELECT {_PROCEDURE_COLUMNS} FROM procedures WHERE id = $1::UUID",
            procedure_id,
        )
        return dict(row) if row else None

    async def search(
        self,
        *,
        embedding: Sequence[float],
        org_id: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            f"""
            SELECT {_PROCEDURE_COLUMNS},
                   1 - (embedding <=> $1::VECTOR) AS similarity
            FROM procedures
            WHERE status = 'active'
              AND ($2::TEXT IS NULL OR org_id = $2)
            ORDER BY embedding <=> $1::VECTOR
            LIMIT $3
            """,
            format_vector(normalize_vector(embedding)),
            org_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def update(self, procedure_id: str, **fields: Any) -> dict[str, Any]:
        sets: list[str] = []
        args: list[Any] = []
        n = 0
        for col, val in fields.items():
            if val == "now()":
                sets.append(f"{col} = now()")
                continue
            n += 1
            sets.append(f"{col} = ${n}")
            args.append(val)
        args.append(procedure_id)
        row = await self.client.fetch_one(
            f"UPDATE procedures SET {', '.join(sets)}, updated_at = now()"
            f" WHERE id = ${n + 1}::UUID RETURNING {_PROCEDURE_COLUMNS}",
            *args,
        )
        return dict(row) if row else {}
