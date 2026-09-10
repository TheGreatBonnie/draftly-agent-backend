"""Persistence for the canonical workflow resource model.

The repository layer is deliberately organization-aware: callers must provide
the verified organization id for every read and write. It also owns cursor
encoding so API handlers cannot accidentally introduce offset pagination.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from draftly.integrations.database.client import DatabaseClient

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


class InvalidWorkflowCursorError(ValueError):
    """Raised when a cursor is malformed or has the wrong sort shape."""


class WorkflowConflictError(ValueError):
    """Raised when a workflow resource violates a domain uniqueness rule."""


def _encode_cursor(*, sort_value: Any, resource_id: str) -> str:
    if isinstance(sort_value, datetime):
        sort_value = sort_value.astimezone(UTC).isoformat()
    raw = json.dumps({"sort": str(sort_value), "id": resource_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            raise ValueError
        sort_value = datetime.fromisoformat(str(payload["sort"]).replace("Z", "+00:00"))
        if sort_value.tzinfo is None:
            sort_value = sort_value.replace(tzinfo=UTC)
        return sort_value.astimezone(UTC), payload["id"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError) as exc:
        raise InvalidWorkflowCursorError("invalid workflow cursor") from exc


InvalidWorkflowCursor = InvalidWorkflowCursorError


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, separators=(",", ":"))


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _page_size(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_unset=True)
    return dict(value)


class WorkflowDefinitionsRepository:
    """Organization-scoped CRUD and summary queries for definitions."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.db = database

    def _database(self) -> DatabaseClient:
        if self.db is None:
            raise RuntimeError("workflow database is unavailable")
        return self.db

    async def list(
        self,
        *,
        org_id: str,
        status: str | None = None,
        workflow_key: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        page_size = _page_size(limit)
        params: list[Any] = [org_id]
        clauses = ["org_id = $1"]
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if workflow_key:
            params.append(workflow_key)
            clauses.append(f"workflow_key = ${len(params)}")
        if cursor:
            updated_at, definition_id = _decode_cursor(cursor)
            params.extend([updated_at, definition_id])
            clauses.append(
                f"(updated_at < ${len(params) - 1} OR "
                f"(updated_at = ${len(params) - 1} AND id::text < ${len(params)}))"
            )

        where = " AND ".join(clauses)
        params.append(page_size)
        rows = await self._database().fetch_all(
            f"""
            SELECT id::text, org_id, slug, name, description, workflow_key, status,
                   version, trigger_config, condition_config, agent_config,
                   repository_config, evaluation_config, review_config, delivery_config,
                   created_by, created_at, updated_at
            FROM workflow_definitions
            WHERE {where}
            ORDER BY updated_at DESC, id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        count_params = params[:-1]
        count_row = await self._database().fetch_one(
            f"SELECT count(*) AS total FROM workflow_definitions WHERE {where}",
            *count_params,
        )
        items = [_row_dict(row) for row in rows]
        next_cursor = None
        if len(items) == page_size and items[-1].get("updated_at") is not None:
            next_cursor = _encode_cursor(
                sort_value=items[-1]["updated_at"],
                resource_id=str(items[-1]["id"]),
            )
        return items, int((count_row or {}).get("total", 0)), next_cursor

    async def get(self, *, org_id: str, workflow_id: str) -> dict[str, Any] | None:
        row = await self._database().fetch_one(
            """
            SELECT id::text, org_id, slug, name, description, workflow_key, status,
                   version, trigger_config, condition_config, agent_config,
                   repository_config, evaluation_config, review_config, delivery_config,
                   created_by, created_at, updated_at
            FROM workflow_definitions
            WHERE org_id = $1 AND id = $2::UUID
            """,
            org_id,
            workflow_id,
        )
        return _row_dict(row) if row is not None else None

    async def find_active_by_key(self, *, org_id: str, workflow_key: str) -> dict[str, Any] | None:
        row = await self._database().fetch_one(
            """
            SELECT id::text, org_id, slug, name, description, workflow_key, status,
                   version, trigger_config, condition_config, agent_config,
                   repository_config, evaluation_config, review_config, delivery_config,
                   created_by, created_at, updated_at
            FROM workflow_definitions
            WHERE org_id = $1 AND workflow_key = $2 AND status = 'active'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            org_id,
            workflow_key,
        )
        return _row_dict(row) if row is not None else None

    async def create(
        self,
        *,
        org_id: str,
        created_by: str | None,
        payload: Any,
    ) -> dict[str, Any]:
        data = _payload(payload)
        row = await self._database().fetch_one(
            """
            INSERT INTO workflow_definitions
                (org_id, slug, name, description, workflow_key, status,
                 trigger_config, condition_config, agent_config, repository_config,
                 evaluation_config, review_config, delivery_config, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7::JSONB, $8::JSONB, $9::JSONB,
                    $10::JSONB, $11::JSONB, $12::JSONB, $13::JSONB, $14)
            RETURNING id::text, org_id, slug, name, description, workflow_key, status,
                      version, trigger_config, condition_config, agent_config,
                      repository_config, evaluation_config, review_config, delivery_config,
                      created_by, created_at, updated_at
            """,
            org_id,
            data["slug"],
            data["name"],
            data.get("description"),
            data["workflow_key"],
            data.get("status", "draft"),
            _json(data.get("trigger_config")),
            _json(data.get("condition_config")),
            _json(data.get("agent_config")),
            _json(data.get("repository_config")),
            _json(data.get("evaluation_config")),
            _json(data.get("review_config")),
            _json(data.get("delivery_config")),
            created_by,
        )
        if row is None:
            raise RuntimeError("workflow definition was not returned after insert")
        return _row_dict(row)

    async def update(
        self,
        *,
        org_id: str,
        workflow_id: str,
        payload: Any,
    ) -> dict[str, Any] | None:
        data = _payload(payload)
        if not data:
            return await self.get(org_id=org_id, workflow_id=workflow_id)
        fields: list[str] = []
        params: list[Any] = [org_id, workflow_id]
        json_fields = {
            "trigger_config",
            "condition_config",
            "agent_config",
            "repository_config",
            "evaluation_config",
            "review_config",
            "delivery_config",
        }
        allowed = {"name", "description", "workflow_key", "status", *json_fields}
        for key, value in data.items():
            if key not in allowed:
                continue
            params.append(_json(value) if key in json_fields else value)
            placeholder = f"${len(params)}"
            fields.append(
                f"{key} = {placeholder}::JSONB" if key in json_fields else f"{key} = {placeholder}"
            )
        fields.extend(["version = version + 1", "updated_at = now()"])
        row = await self._database().fetch_one(
            f"""
            UPDATE workflow_definitions
            SET {", ".join(fields)}
            WHERE org_id = $1 AND id = $2::UUID AND status <> 'archived'
            RETURNING id::text, org_id, slug, name, description, workflow_key, status,
                      version, trigger_config, condition_config, agent_config,
                      repository_config, evaluation_config, review_config, delivery_config,
                      created_by, created_at, updated_at
            """,
            *params,
        )
        return _row_dict(row) if row is not None else None

    async def set_status(
        self,
        *,
        org_id: str,
        workflow_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        row = await self._database().fetch_one(
            """
            UPDATE workflow_definitions
            SET status = $3, version = version + 1, updated_at = now()
            WHERE org_id = $1 AND id = $2::UUID AND status <> 'archived'
            RETURNING id::text, org_id, slug, name, description, workflow_key, status,
                      version, trigger_config, condition_config, agent_config,
                      repository_config, evaluation_config, review_config, delivery_config,
                      created_by, created_at, updated_at
            """,
            org_id,
            workflow_id,
            status,
        )
        return _row_dict(row) if row is not None else None

    async def summary(self, *, org_id: str, days: int = 30) -> dict[str, Any]:
        window = max(1, min(int(days), 365))
        database = self._database()
        run_row = await database.fetch_one(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status IN ('queued', 'running')) AS active,
                   count(*) FILTER (WHERE status = 'completed') AS successful,
                   count(*) FILTER (WHERE status = 'failed') AS failed,
                   count(*) FILTER (WHERE status = 'pending_review') AS pending_review,
                   count(*) FILTER (WHERE status = 'pending_intervention') AS pending_intervention,
                   COALESCE(avg(EXTRACT(EPOCH FROM (completed_at - started_at)))
                       FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL), 0)
                       AS avg_duration_seconds
            FROM workflow_runs
            WHERE org_id = $1 AND created_at >= now() - ($2 * INTERVAL '1 day')
            """,
            org_id,
            window,
        )
        definition_row = await database.fetch_one(
            """
            SELECT count(*) FILTER (WHERE status = 'active') AS active,
                   count(*) FILTER (WHERE status = 'paused') AS paused,
                   count(*) FILTER (WHERE status = 'draft') AS draft,
                   count(*) FILTER (WHERE status = 'archived') AS archived
            FROM workflow_definitions WHERE org_id = $1
            """,
            org_id,
        )
        runs = _row_dict(run_row)
        definitions = _row_dict(definition_row)
        total = int(runs.get("total", 0))
        successful = int(runs.get("successful", 0))
        return {
            "definitions": {
                key: int(definitions.get(key, 0))
                for key in ("active", "paused", "draft", "archived")
            },
            "runs": {
                "total": total,
                "active": int(runs.get("active", 0)),
                "successful": successful,
                "failed": int(runs.get("failed", 0)),
                "pending_review": int(runs.get("pending_review", 0)),
                "pending_intervention": int(runs.get("pending_intervention", 0)),
                "success_rate": round(successful / total * 100, 1) if total else 0.0,
                "avg_duration_seconds": float(runs.get("avg_duration_seconds", 0) or 0),
                "days": window,
            },
        }


class WorkflowTemplatesRepository:
    """System-visible and organization-owned template access."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.db = database

    def _database(self) -> DatabaseClient:
        if self.db is None:
            raise RuntimeError("workflow database is unavailable")
        return self.db

    async def list(self, *, org_id: str, limit: int = DEFAULT_PAGE_SIZE) -> list[dict[str, Any]]:
        rows = await self._database().fetch_all(
            """
            SELECT id::text, org_id, slug, name, description, workflow_key, defaults,
                   is_system, created_at, updated_at
            FROM workflow_templates
            WHERE is_system = true OR org_id = $1
            ORDER BY is_system DESC, updated_at DESC, id DESC
            LIMIT $2
            """,
            org_id,
            _page_size(limit),
        )
        return [_row_dict(row) for row in rows]

    async def get(self, *, org_id: str, template_id: str) -> dict[str, Any] | None:
        row = await self._database().fetch_one(
            """
            SELECT id::text, org_id, slug, name, description, workflow_key, defaults,
                   is_system, created_at, updated_at
            FROM workflow_templates
            WHERE id = $1::UUID AND (is_system = true OR org_id = $2)
            """,
            template_id,
            org_id,
        )
        return _row_dict(row) if row is not None else None

    async def create(self, *, org_id: str, payload: Any) -> dict[str, Any]:
        data = _payload(payload)
        row = await self._database().fetch_one(
            """
            INSERT INTO workflow_templates
                (org_id, slug, name, description, workflow_key, defaults, is_system)
            VALUES ($1, $2, $3, $4, $5, $6::JSONB, false)
            RETURNING id::text, org_id, slug, name, description, workflow_key, defaults,
                      is_system, created_at, updated_at
            """,
            org_id,
            data["slug"],
            data["name"],
            data.get("description"),
            data["workflow_key"],
            _json(data.get("defaults")),
        )
        if row is None:
            raise RuntimeError("workflow template was not returned after insert")
        return _row_dict(row)


class WorkflowRunsRepository:
    """Canonical run reads and state transitions."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.db = database

    def _database(self) -> DatabaseClient:
        if self.db is None:
            raise RuntimeError("workflow database is unavailable")
        return self.db

    async def get(self, *, org_id: str, run_id: str) -> dict[str, Any] | None:
        row = await self._database().fetch_one(
            """
            SELECT id, definition_id::text, org_id, source, source_event_id, event_type,
                   title, repository, actor, target, status, current_stage, stage_states,
                   input_data AS input, output_data AS output, error, started_at,
                   completed_at, created_at, updated_at
            FROM workflow_runs
            WHERE id = $1 AND org_id = $2
            """,
            run_id,
            org_id,
        )
        return _row_dict(row) if row is not None else None

    async def list(
        self,
        *,
        org_id: str,
        definition_id: str | None = None,
        status: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        page_size = _page_size(limit)
        params: list[Any] = [org_id]
        clauses = ["org_id = $1"]
        if definition_id:
            params.append(definition_id)
            clauses.append(f"definition_id = ${len(params)}::UUID")
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if cursor:
            created_at, run_id = _decode_cursor(cursor)
            params.extend([created_at, run_id])
            clauses.append(
                f"(created_at < ${len(params) - 1} OR "
                f"(created_at = ${len(params) - 1} AND id < ${len(params)}))"
            )
        where = " AND ".join(clauses)
        params.append(page_size)
        select = """
            SELECT id, definition_id::text, org_id, source, source_event_id, event_type,
                   title, repository, actor, target, status, current_stage, stage_states,
                   input_data AS input, output_data AS output, error, started_at,
                   completed_at, created_at, updated_at
            FROM workflow_runs
        """
        rows = await self._database().fetch_all(
            f"{select} WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ${len(params)}",
            *params,
        )
        count_row = await self._database().fetch_one(
            f"SELECT count(*) AS total FROM workflow_runs WHERE {where}", *params[:-1]
        )
        items = [_row_dict(row) for row in rows]
        next_cursor = None
        if len(items) == page_size and items[-1].get("created_at") is not None:
            next_cursor = _encode_cursor(
                sort_value=items[-1]["created_at"], resource_id=str(items[-1]["id"])
            )
        return items, int((count_row or {}).get("total", 0)), next_cursor

    async def list_for_definition(
        self,
        *,
        org_id: str,
        definition_id: str,
        status: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        page_size = _page_size(limit)
        params: list[Any] = [org_id, definition_id]
        clauses = ["org_id = $1", "definition_id = $2::UUID"]
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if cursor:
            created_at, run_id = _decode_cursor(cursor)
            params.extend([created_at, run_id])
            clauses.append(
                f"(created_at < ${len(params) - 1} OR "
                f"(created_at = ${len(params) - 1} AND id < ${len(params)}))"
            )
        where = " AND ".join(clauses)
        params.append(page_size)
        rows = await self._database().fetch_all(
            f"""
            SELECT id, definition_id::text, org_id, source, source_event_id, event_type,
                   title, repository, actor, target, status, current_stage, stage_states,
                   input_data AS input, output_data AS output, error, started_at,
                   completed_at, created_at, updated_at
            FROM workflow_runs WHERE {where}
            ORDER BY created_at DESC, id DESC LIMIT ${len(params)}
            """,
            *params,
        )
        count_row = await self._database().fetch_one(
            f"SELECT count(*) AS total FROM workflow_runs WHERE {where}",
            *params[:-1],
        )
        items = [_row_dict(row) for row in rows]
        next_cursor = None
        if len(items) == page_size and items[-1].get("created_at") is not None:
            next_cursor = _encode_cursor(
                sort_value=items[-1]["created_at"], resource_id=str(items[-1]["id"])
            )
        return items, int((count_row or {}).get("total", 0)), next_cursor

    async def start_or_get_idempotent(
        self,
        *,
        org_id: str,
        definition_id: str | None,
        source: str,
        source_event_id: str | None,
        title: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        run_id = str(
            metadata.get("run_id") or f"run_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        )
        row = await self._database().fetch_one(
            """
            INSERT INTO workflow_runs
                (id, definition_id, org_id, source, source_event_id, event_type,
                 title, repository, actor, target, status, input_data, started_at)
            VALUES ($1, $2::UUID, $3, $4, $5, $6, $7, $8, $9, $10::JSONB,
                    'queued', $11::JSONB, NULL)
            ON CONFLICT (org_id, source, source_event_id) WHERE source_event_id IS NOT NULL
            DO UPDATE SET updated_at = workflow_runs.updated_at
            RETURNING id, definition_id::text, org_id, source, source_event_id, event_type,
                      title, repository, actor, target, status, current_stage, stage_states,
                      input_data AS input, output_data AS output, error, started_at,
                      completed_at, created_at, updated_at
            """,
            run_id,
            definition_id,
            org_id,
            source,
            source_event_id,
            metadata.get("event_type"),
            title,
            metadata.get("repository"),
            metadata.get("actor"),
            _json(metadata.get("target")),
            _json(metadata.get("input")),
        )
        if row is None:
            raise RuntimeError("workflow run was not returned after insert")
        return _row_dict(row)

    async def update_state(
        self,
        *,
        org_id: str,
        run_id: str,
        status: str,
        current_stage: str | None = None,
        stage_states: dict[str, Any] | None = None,
        error: str | None = None,
        output: dict[str, Any] | None = None,
    ) -> None:
        completed = status in {"completed", "failed", "cancelled", "skipped"}
        await self._database().execute(
            """
            UPDATE workflow_runs
            SET status = $3, current_stage = COALESCE($4, current_stage),
                stage_states = COALESCE($5::JSONB, stage_states),
                error = $6, output_data = COALESCE($7::JSONB, output_data),
                started_at = CASE WHEN $3 = 'running' AND started_at IS NULL
                                  THEN now() ELSE started_at END,
                completed_at = CASE WHEN $8 THEN COALESCE(completed_at, now()) ELSE NULL END,
                updated_at = now()
            WHERE id = $1 AND org_id = $2
            """,
            run_id,
            org_id,
            status,
            current_stage,
            _json(stage_states) if stage_states is not None else None,
            error,
            _json(output) if output is not None else None,
            completed,
        )

    async def list_steps(self, *, org_id: str, run_id: str) -> list[dict[str, Any]]:
        rows = await self._database().fetch_all(
            """
            SELECT s.seq, s.kind, s.name, s.status, s.duration_ms, s.detail, s.created_at
            FROM agent_steps s
            JOIN workflow_runs r ON r.id = s.run_id
            WHERE r.org_id = $1 AND r.id = $2
            ORDER BY s.seq ASC, s.created_at ASC
            """,
            org_id,
            run_id,
        )
        return [_row_dict(row) for row in rows]

    async def list_artifacts(self, *, org_id: str, run_id: str) -> list[dict[str, Any]]:
        """Return display-safe delivery artifacts when a delivery row exists."""
        rows = await self._database().fetch_all(
            """
            SELECT dp.run_id, dp.status, dp.created_at
            FROM delivery_plans dp
            JOIN workflow_runs r ON r.id = dp.run_id
            WHERE r.org_id = $1 AND r.id = $2
            ORDER BY dp.created_at ASC
            """,
            org_id,
            run_id,
        )
        return [_row_dict(row) for row in rows]


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "InvalidWorkflowCursor",
    "InvalidWorkflowCursorError",
    "MAX_PAGE_SIZE",
    "WorkflowConflictError",
    "WorkflowDefinitionsRepository",
    "WorkflowRunsRepository",
    "WorkflowTemplatesRepository",
]
