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
        surface: str = "",
        workflow_key: str | None = None,
        definition_id: str | None = None,
    ) -> None:
        if self.database is None:
            return
        await self.database.execute(
            """
            INSERT INTO agent_runs
                (run_id, source, event_type, org_id, status, surface, workflow_key, definition_id)
            VALUES ($1, $2, $3, $4, 'running', $5, $6, $7)
            ON CONFLICT (run_id) DO UPDATE
            SET status = 'running', started_at = now(), completed_at = NULL,
                source = EXCLUDED.source, event_type = EXCLUDED.event_type,
                org_id = EXCLUDED.org_id, surface = EXCLUDED.surface,
                workflow_key = EXCLUDED.workflow_key,
                definition_id = EXCLUDED.definition_id
            """,
            run_id,
            source,
            event_type,
            org_id,
            surface,
            workflow_key,
            definition_id,
        )

    async def record_step(
        self,
        *,
        run_id: str,
        seq: int | None = None,
        kind: str = "node",
        name: str,
        status: str = "completed",
        duration_ms: int | None = None,
        detail: dict[str, Any] | None = None,
        agent_id: str | None = None,
        node_id: str | None = None,
        surface: str = "",
    ) -> None:
        if self.database is None:
            return
        await self.database.execute(
            """
            INSERT INTO agent_steps
                (run_id, seq, kind, name, status, duration_ms, detail, agent_id, node_id, surface)
            VALUES ($1, $2, $3, $4, $5, $6, $7::JSONB, $8, $9, $10)
            """,
            run_id,
            seq if seq is not None else 0,
            kind,
            name,
            status,
            duration_ms,
            _to_json(detail),
            agent_id,
            node_id,
            surface,
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
                   surface, workflow_key, definition_id, started_at, completed_at
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
                   surface, workflow_key, definition_id, started_at, completed_at
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
            SELECT seq, kind, name, status, duration_ms, detail,
                   agent_id, node_id, surface
            FROM agent_steps WHERE run_id = $1 ORDER BY seq ASC
            """,
            run_id,
        )
        return [dict(row) for row in rows]

    async def list_agent_summaries(
        self, *, org_id: str, surface: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return one aggregate row per catalog agent, including zero-run agents."""
        if self.database is None:
            return []
        from draftly.agents.catalog import expand_tool_keys, list_agent_descriptors

        params: list[Any] = [org_id]
        params.append(max(1, min(limit, 200)))
        rows = await self.database.fetch_all(
            f"""
            SELECT s.agent_id, s.surface AS step_surface, s.status AS step_status,
                   ar.run_id, ar.status AS run_status, ar.started_at,
                   ar.completed_at, ar.event_type
            FROM agent_steps s
            JOIN agent_runs ar ON ar.run_id = s.run_id
            WHERE ar.org_id = $1
            ORDER BY ar.started_at DESC NULLS LAST, ar.run_id DESC, s.seq DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            grouped.setdefault(str(item.get("agent_id") or "legacy"), []).append(item)
        summaries: list[dict[str, Any]] = []
        for descriptor in list_agent_descriptors():
            items = grouped.get(descriptor.id, [])
            latest = items[0] if items else None
            terminal = [item for item in items if item.get("run_status") in {"completed", "failed"}]
            successful = sum(item.get("run_status") == "completed" for item in terminal)
            summaries.append({
                "id": descriptor.id,
                "role": descriptor.role,
                "name": descriptor.name,
                "description": descriptor.description,
                "surface": descriptor.surface,
                "tools": expand_tool_keys(descriptor.tool_keys),
                "availability": "enabled",
                "last_run_status": str(latest.get("run_status") if latest else "idle"),
                "runs_7d": len({item.get("run_id") for item in items}),
                "success_rate_7d": successful / len(terminal) if terminal else None,
                "last_run_at": latest.get("started_at") if latest else None,
                "latest_run_id": latest.get("run_id") if latest else None,
                "legacy_steps": 0,
            })
        if grouped.get("legacy"):
            summaries.append({
                "id": "legacy",
                "role": "legacy",
                "name": "Legacy telemetry",
                "description": "Historical steps without stable agent identity.",
                "surface": "legacy",
                "tools": [],
                "availability": "enabled",
                "last_run_status": "unknown",
                "runs_7d": len({item.get("run_id") for item in grouped["legacy"]}),
                "success_rate_7d": None,
                "last_run_at": grouped["legacy"][0].get("started_at"),
                "latest_run_id": grouped["legacy"][0].get("run_id"),
                "legacy_steps": len(grouped["legacy"]),
            })
        if surface:
            summaries = [item for item in summaries if item["surface"] == surface]
        return summaries

    async def get_agent_detail(
        self, *, org_id: str, agent_id: str, window_days: int = 30
    ) -> dict[str, Any] | None:
        from draftly.agents.catalog import expand_tool_keys, get_agent_descriptor

        descriptor = get_agent_descriptor(agent_id)
        if descriptor is None:
            return None
        summaries = await self.list_agent_summaries(org_id=org_id, limit=200)
        summary = next(item for item in summaries if item["id"] == agent_id)
        runs, _ = await self.list_agent_runs(org_id=org_id, agent_id=agent_id, limit=20)
        return {
            "agent": summary,
            "metrics": {
                "window_days": window_days,
                "runs": summary["runs_7d"],
                "success_rate": summary["success_rate_7d"],
            },
            "tools": expand_tool_keys(descriptor.tool_keys),
            "recent_runs": runs,
        }

    async def list_agent_runs(
        self, *, org_id: str, agent_id: str, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        if self.database is None:
            return [], None
        params: list[Any] = [org_id, agent_id]
        cursor_clause = ""
        if cursor:
            import base64
            import json
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                params.extend([decoded["started_at"], decoded["run_id"]])
                cursor_clause = (
                    f" AND (ar.started_at, ar.run_id) < "
                    f"(${len(params) - 1}, ${len(params)})"
                )
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                return [], None
        params.append(max(1, min(limit, 200)) + 1)
        rows = await self.database.fetch_all(
            f"""
            SELECT DISTINCT ar.run_id, ar.source, ar.event_type, ar.org_id, ar.status,
                   ar.error, ar.surface, ar.started_at, ar.completed_at
            FROM agent_runs ar
            JOIN agent_steps s ON s.run_id = ar.run_id AND s.agent_id = $2
            WHERE ar.org_id = $1{cursor_clause}
            ORDER BY ar.started_at DESC, ar.run_id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        has_more = len(rows) > params[-1] - 1
        rows = rows[: params[-1] - 1]
        next_cursor = None
        if has_more and rows:
            import base64
            import json
            last = dict(rows[-1])
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {"started_at": str(last.get("started_at")), "run_id": last["run_id"]}
                ).encode()
            ).decode()
        return [dict(row) for row in rows], next_cursor


def _to_json(value: dict[str, Any] | None) -> str:
    import json

    return json.dumps(value or {})
