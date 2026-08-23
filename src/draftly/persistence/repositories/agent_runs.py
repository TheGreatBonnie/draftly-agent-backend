"""Agent run audit repository (plan §10.2).

Writes ``agent_runs`` / ``agent_steps`` rows covering the full run
lifecycle: trigger → evidence → agents → tool calls → generated
artifact → evaluation → approval → delivery.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from draftly.integrations.database.client import DatabaseClient

logger = structlog.get_logger(__name__)


class AgentRunsRepository:
    """Persistence for per-run audit rows."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database

    async def start_run(
        self,
        *,
        run_id: str,
        source: str = "github",
        event_type: str = "unknown",
        org_id: str = "",
    ) -> None:
        if self.database is None:
            return
        await self.database.execute(
            """
            INSERT INTO agent_runs (run_id, source, event_type, org_id, status)
            VALUES ($1, $2, $3, $4, 'running')
            ON CONFLICT (run_id) DO UPDATE
            SET status = 'running', started_at = now(), completed_at = NULL
            """,
            run_id,
            source,
            event_type,
            org_id,
        )

    async def record_step(
        self,
        *,
        run_id: str,
        seq: int,
        kind: str = "node",
        name: str,
        status: str = "completed",
        duration_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self.database is None:
            return
        await self.database.execute(
            """
            INSERT INTO agent_steps (run_id, seq, kind, name, status, duration_ms, detail)
            VALUES ($1, $2, $3, $4, $5, $6, $7::JSONB)
            """,
            run_id,
            seq,
            kind,
            name,
            status,
            duration_ms,
            _to_json(detail),
        )

    async def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        if self.database is None:
            return
        await self.database.execute(
            """
            UPDATE agent_runs
            SET status = $2, error = $3, completed_at = $4
            WHERE run_id = $1
            """,
            run_id,
            status,
            error,
            datetime.now(UTC),
        )

    # --------------------------------------------------------------
    # Read-side APIs (spec §Observability surface #2)
    # --------------------------------------------------------------

    async def list_runs(
        self,
        *,
        org_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self.database is None:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if org_id:
            params.append(org_id)
            clauses.append(f"org_id = ${len(params)}")
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))
        rows = await self.database.fetch_all(
            f"""
            SELECT run_id, source, event_type, org_id, status, error,
                   started_at, completed_at
            FROM agent_runs {where}
            ORDER BY started_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        return [dict(row) for row in rows]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        if self.database is None:
            return None
        row = await self.database.fetch_one(
            """
            SELECT run_id, source, event_type, org_id, status, error,
                   started_at, completed_at
            FROM agent_runs WHERE run_id = $1
            """,
            run_id,
        )
        return dict(row) if row else None

    async def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        if self.database is None:
            return []
        rows = await self.database.fetch_all(
            """
            SELECT seq, kind, name, status, duration_ms, detail
            FROM agent_steps WHERE run_id = $1 ORDER BY seq ASC
            """,
            run_id,
        )
        return [dict(row) for row in rows]


def _to_json(value: dict[str, Any] | None) -> str:
    import json

    return json.dumps(value or {})
