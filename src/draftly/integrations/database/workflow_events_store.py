"""Durable workflow-event log for SSE replay (spec §Phase 2).

One row per published envelope, keyed ``(run_id, seq)``. Enables
Last-Event-ID resume for SSE consumers and post-run replay/analysis.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from draftly.integrations.database.client import DatabaseClient


class WorkflowEventsStore:
    def __init__(self, client: DatabaseClient | None = None) -> None:
        self.client = client or DatabaseClient()

    async def append(self, envelope: dict[str, Any]) -> None:
        raw_ts = envelope.get("ts")
        if isinstance(raw_ts, str) and raw_ts.strip():
            ts = datetime.fromisoformat(raw_ts).astimezone(UTC).replace(tzinfo=None)
        else:
            ts = datetime.now(UTC).replace(tzinfo=None)
        await self.client.execute(
            """
            INSERT INTO workflow_events (run_id, seq, ts, type, node_id, payload)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (run_id, seq) DO NOTHING
            """,
            envelope.get("run_id", ""),
            int(envelope.get("seq", 0)),
            ts,
            str(envelope.get("type", "unknown")),
            envelope.get("node_id"),
            json.dumps(envelope.get("payload") or {}, default=str),
        )

    async def list_after(
        self,
        run_id: str,
        *,
        seq: int,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows = await self.client.fetch_all(
            """
            SELECT run_id, seq, ts, type, node_id, payload
            FROM workflow_events
            WHERE run_id = $1 AND seq > $2
            ORDER BY seq ASC
            LIMIT $3
            """,
            run_id,
            seq,
            limit,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            payload = item.get("payload")
            if isinstance(payload, str):
                item["payload"] = json.loads(payload)
            out.append(item)
        return out

    async def terminal_run_ids(self, run_ids: list[str]) -> set[str]:
        """Distinct run_ids that already have a terminal ``workflow_result`` envelope."""
        if not run_ids:
            return set()
        rows = await self.client.fetch_all(
            """
            SELECT DISTINCT run_id
            FROM workflow_events
            WHERE run_id = ANY($1::TEXT[]) AND type = 'workflow_result'
            """,
            list(run_ids),
        )
        return {str(row["run_id"]) for row in rows}
