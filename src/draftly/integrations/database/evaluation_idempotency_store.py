from __future__ import annotations

import json
from typing import Any

from draftly.integrations.database.client import DatabaseClient


class IdempotencyKeyConflictError(ValueError):
    """The same organization/key was reused for a different request."""


class EvaluationIdempotencyStore:
    def __init__(self, client: DatabaseClient) -> None:
        self.client = client

    async def get(self, *, org_id: str, idempotency_key: str) -> dict[str, Any] | None:
        row = await self.client.fetch_one(
            """
            SELECT org_id, idempotency_key, request_hash, run_id, response
            FROM evaluation_run_idempotency
            WHERE org_id = $1 AND idempotency_key = $2 AND expires_at > now()
            """,
            org_id,
            idempotency_key,
        )
        return dict(row) if row else None

    async def create(
        self,
        *,
        org_id: str,
        idempotency_key: str,
        request_hash: str,
        run_id: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        row = await self.client.fetch_one(
            """
            INSERT INTO evaluation_run_idempotency
                (org_id, idempotency_key, request_hash, run_id, response)
            VALUES ($1, $2, $3, $4, $5::JSONB)
            ON CONFLICT (org_id, idempotency_key) DO NOTHING
            RETURNING org_id, idempotency_key, request_hash, run_id, response
            """,
            org_id,
            idempotency_key,
            request_hash,
            run_id,
            json.dumps(response),
        )
        if row:
            return dict(row)
        existing = await self.get(org_id=org_id, idempotency_key=idempotency_key)
        if existing is None:
            raise RuntimeError("idempotency record was not created")
        if existing.get("request_hash") != request_hash:
            raise IdempotencyKeyConflictError(
                "Idempotency-Key was reused for another request"
            )
        return existing
