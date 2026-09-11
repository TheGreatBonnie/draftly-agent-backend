"""Durable steering attempts and human interventions.

``steering_attempts`` holds bounded auto-guide counters per (run, agent, node,
phase, tool, model turn): :meth:`SteeringAttemptsRepository.reserve` atomically
increments ``guide_count`` only while it stays below the requested limit, making
concurrent budget exhaustion safe. ``workflow_interventions`` is the durable
human-intervention inbox; claims lock the pending row and are idempotent by
(org, idempotency_key).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from draftly.integrations.database.client import DatabaseClient


class InterventionConflictError(ValueError):
    """A resolved intervention was answered with a different response."""


class InterventionNotFoundError(LookupError):
    """No pending intervention matched the claim/resolution target."""


class InvalidInterventionActionError(ValueError):
    """The claim action does not map to an intervention status."""


@dataclass(frozen=True)
class AttemptKey:
    """Primary key of one steering-attempt counter row."""

    run_id: str
    agent_id: str
    node_id: str
    phase: str
    tool_name: str = ""
    model_turn: int = 0


@dataclass
class InterventionRecord:
    """One durable human intervention row."""

    id: str | None = None
    run_id: str = ""
    interrupt_id: str = ""
    org_id: str = ""
    surface: str = ""
    workflow_key: str | None = None
    agent_id: str = ""
    node_id: str = ""
    tool_name: str = ""
    status: str = "pending"
    reason: dict[str, Any] = field(default_factory=dict)
    response_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    resolver_id: str | None = None
    created_at: Any = None
    updated_at: Any = None
    resolved_at: Any = None
    expires_at: Any = None

    def model_dump(self, *, exclude_none: bool = True) -> dict[str, Any]:
        dump = asdict(self)
        if exclude_none:
            return {key: value for key, value in dump.items() if value is not None}
        return dump


_SELECT = """
    SELECT id::text, run_id, interrupt_id, org_id, surface, workflow_key,
           agent_id, node_id, tool_name, status, reason, response_message,
           metadata, idempotency_key, resolver_id, created_at, updated_at,
           resolved_at, expires_at
    FROM workflow_interventions
"""


def _record(row: Any | None) -> InterventionRecord | None:
    if row is None:
        return None
    return InterventionRecord(**dict(row))


_ACTION_STATUS = {
    "approve": "approved",
    "approve_and_review": "approved",
    "deny": "denied",
    "denied": "denied",
    "guide": "guided",
    "cancel": "cancelled",
}


class SteeringAttemptsRepository:
    """Bounded, atomic auto-guide budget counters."""

    def __init__(self, database: DatabaseClient) -> None:
        self.database = database

    async def reserve(self, *, key: AttemptKey, limit: int) -> bool:
        """Atomically reserve one guide when the prior count is below ``limit``.

        The single upsert inserts the counter at 1 or, on conflict, increments
        ``guide_count`` guarded by ``guide_count < limit``. ``RETURNING`` a row
        means the reservation succeeded; an exhausted budget returns no row.
        """
        row = await self.database.fetch_one(
            """
            INSERT INTO steering_attempts
                (run_id, agent_id, node_id, phase, tool_name, model_turn,
                 guide_count, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, 1, now())
            ON CONFLICT (run_id, agent_id, node_id, phase, tool_name, model_turn)
            DO UPDATE SET guide_count = steering_attempts.guide_count + 1,
                          updated_at = now()
            WHERE steering_attempts.guide_count < $7
            RETURNING guide_count
            """,
            key.run_id,
            key.agent_id,
            key.node_id,
            key.phase,
            key.tool_name,
            key.model_turn,
            limit,
        )
        return row is not None


class SteeringInterventionsRepository:
    """Durable human-intervention inbox with atomic, idempotent claims."""

    def __init__(self, database: DatabaseClient) -> None:
        self.database = database

    async def create_pending(self, *, record: InterventionRecord) -> InterventionRecord:
        """Insert a pending intervention, returning any existing duplicate."""
        row = await self.database.fetch_one(
            """
            INSERT INTO workflow_interventions
                (run_id, interrupt_id, org_id, surface, workflow_key, agent_id,
                 node_id, tool_name, status, reason, response_message, metadata,
                 idempotency_key, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending', $9::JSONB, $10,
                    $11::JSONB, $12, $13)
            ON CONFLICT (run_id, interrupt_id) DO NOTHING
            RETURNING id::text, run_id, interrupt_id, org_id, surface,
                      workflow_key, agent_id, node_id, tool_name, status, reason,
                      response_message, metadata, idempotency_key, resolver_id,
                      created_at, updated_at, resolved_at, expires_at
            """,
            record.run_id,
            record.interrupt_id,
            record.org_id,
            record.surface,
            record.workflow_key,
            record.agent_id,
            record.node_id,
            record.tool_name,
            _json(record.reason),
            record.response_message,
            _json(record.metadata),
            record.idempotency_key,
            record.expires_at,
        )
        if row is not None:
            return InterventionRecord(**dict(row))
        existing = await self.database.fetch_one(
            f"""
            {_SELECT}
            WHERE run_id = $1 AND interrupt_id = $2 AND org_id = $3
            """,
            record.run_id,
            record.interrupt_id,
            record.org_id,
        )
        if existing is None:
            raise InterventionNotFoundError(f"intervention {record.interrupt_id} is missing")
        return InterventionRecord(**dict(existing))

    async def claim_response(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        org_id: str,
        idempotency_key: str,
        action: str,
        message: str | None,
    ) -> InterventionRecord:
        """Claim and resolve a pending intervention idempotently.

        A resolved row for the same ``idempotency_key`` is replayed verbatim
        when the response matches; a different response after resolution raises
        :class:`InterventionConflictError`. Otherwise the pending row is updated
        atomically (single-writer) to the mapped status.
        """
        status = _ACTION_STATUS.get(action)
        if status is None:
            raise InvalidInterventionActionError(f"unsupported claim action '{action}'")

        replay = await self.database.fetch_one(
            f"""
            {_SELECT}
            WHERE org_id = $1 AND idempotency_key = $2 AND status <> 'pending'
            """,
            org_id,
            idempotency_key,
        )
        if replay is not None:
            record = InterventionRecord(**dict(replay))
            if record.resolver_id != action or record.response_message != message:
                raise InterventionConflictError(
                    f"intervention {replay['id']} already resolved with a different response"
                )
            return record

        row = await self.database.fetch_one(
            """
            UPDATE workflow_interventions
            SET status = $1, resolver_id = $5, response_message = $6,
                idempotency_key = COALESCE(idempotency_key, $7),
                resolved_at = now(), updated_at = now()
            WHERE run_id = $2 AND interrupt_id = $3 AND org_id = $4
                  AND status = 'pending'
            RETURNING id::text, run_id, interrupt_id, org_id, surface,
                      workflow_key, agent_id, node_id, tool_name, status, reason,
                      response_message, metadata, idempotency_key, resolver_id,
                      created_at, updated_at, resolved_at, expires_at
            """,
            status,
            run_id,
            interrupt_id,
            org_id,
            action,
            message,
            idempotency_key,
        )
        if row is not None:
            return InterventionRecord(**dict(row))

        await self.database.fetch_one(
            f"""
            {_SELECT}
            WHERE run_id = $1 AND interrupt_id = $2 AND org_id = $3
            """,
            run_id,
            interrupt_id,
            org_id,
        )
        raise InterventionNotFoundError(
            f"no pending intervention for run {run_id} interrupt {interrupt_id}"
        )

    async def resolve(
        self,
        *,
        intervention_id: str,
        status: str,
        resolver_id: str | None = None,
    ) -> InterventionRecord:
        """Resolve a pending intervention by id."""
        row = await self.database.fetch_one(
            """
            UPDATE workflow_interventions
            SET status = $1, resolver_id = COALESCE($3, resolver_id),
                resolved_at = now(), updated_at = now()
            WHERE id = $2::UUID AND status = 'pending'
            """,
            status,
            intervention_id,
            resolver_id,
        )
        if row is None:
            raise InterventionNotFoundError(
                f"no pending intervention with id {intervention_id}"
            )
        return InterventionRecord(**dict(row))

    async def get_pending(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        org_id: str,
    ) -> InterventionRecord | None:
        row = await self.database.fetch_one(
            f"""
            {_SELECT}
            WHERE run_id = $1 AND interrupt_id = $2 AND org_id = $3
                  AND status = 'pending'
            """,
            run_id,
            interrupt_id,
            org_id,
        )
        return _record(row)

    async def list_pending_for_run(
        self,
        *,
        run_id: str,
        org_id: str,
    ) -> list[InterventionRecord]:
        rows = await self.database.fetch_all(
            f"""
            {_SELECT}
            WHERE run_id = $1 AND org_id = $2 AND status = 'pending'
            ORDER BY created_at ASC
            """,
            run_id,
            org_id,
        )
        return [InterventionRecord(**dict(row)) for row in rows]


def _json(value: Any) -> str:
    import json

    return json.dumps(value if value is not None else {}, separators=(",", ":"))


__all__ = [
    "AttemptKey",
    "InterventionConflictError",
    "InterventionNotFoundError",
    "InterventionRecord",
    "InvalidInterventionActionError",
    "SteeringAttemptsRepository",
    "SteeringInterventionsRepository",
]
