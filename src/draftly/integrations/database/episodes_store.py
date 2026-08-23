"""Async episodes store backed by the episodes table."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from draftly.integrations.database.client import DatabaseClient
from draftly.memory.vector_utils import format_vector, normalize_vector

_EPISODE_COLUMNS = """id, org_id, agent_run_id, trigger_type, trigger_id,
    trigger_summary, actions_taken, tools_used, outcome, evaluation_results,
    artifacts_created, summary, created_at"""


class EpisodesStore:
    def __init__(self, client: DatabaseClient | None = None) -> None:
        self.client = client or DatabaseClient()

    async def insert(self, *, fields: dict[str, Any]) -> dict[str, Any]:
        embedding = normalize_vector(fields.pop("embedding"))
        evaluation = fields.get("evaluation_results")
        row = await self.client.fetch_one(
            """
            INSERT INTO episodes (
                org_id, agent_run_id, trigger_type, trigger_id,
                trigger_summary, actions_taken, tools_used, outcome,
                evaluation_results, artifacts_created, summary, embedding
            ) VALUES ($1,$2,$3,$4,$5,$6::JSONB,$7,$8,$9::JSONB,$10::JSONB,$11,$12::VECTOR)
            RETURNING id, org_id, agent_run_id, trigger_type, trigger_id,
                trigger_summary, actions_taken, tools_used, outcome,
                evaluation_results, artifacts_created, summary, created_at
            """,
            fields.get("org_id"),
            fields.get("agent_run_id"),
            fields["trigger_type"],
            fields.get("trigger_id"),
            fields["trigger_summary"],
            json.dumps(fields.get("actions_taken") or []),
            list(fields.get("tools_used") or []),
            fields["outcome"],
            json.dumps(evaluation) if evaluation is not None else None,
            json.dumps(fields.get("artifacts_created") or []),
            fields.get("summary"),
            format_vector(embedding),
        )
        return dict(row) if row else {}

    async def search(
        self,
        *,
        embedding: Sequence[float],
        org_id: str | None = None,
        limit: int = 5,
        recent_only: bool = True,
    ) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            f"""
            SELECT {_EPISODE_COLUMNS},
                   1 - (embedding <=> $1::VECTOR) AS similarity
            FROM episodes
            WHERE ($2::TEXT IS NULL OR org_id = $2)
              AND ($4::BOOL = FALSE OR created_at > now()
                   - ($3::TEXT || ' days')::INTERVAL)
            ORDER BY embedding <=> $1::VECTOR
            LIMIT $5
            """,
            format_vector(normalize_vector(embedding)),
            org_id,
            "180",
            recent_only,
            limit,
        )
        return [dict(r) for r in rows]
