"""Task 3: durable attempt reservation and intervention claim/reply persistence."""

from __future__ import annotations

from typing import Any

import pytest

from draftly.persistence.repositories.steering import (
    AttemptKey,
    InterventionConflictError,
    InterventionNotFoundError,
    InterventionRecord,
    SteeringAttemptsRepository,
    SteeringInterventionsRepository,
)
from draftly.steering.persistence import SteeringPersistence


class FakeClient:
    """Mirrors the real client's fetch_one/execute surface (asyncpg style)."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_results: list[dict[str, Any] | None] = []
        self.repeat: dict[str, dict[str, Any] | None] = {}

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append(query)
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetched.append((query, args))
        lowered = query.lower()
        for trigger, row in self.repeat.items():
            if trigger in lowered:
                return row
        if self._fetch_results:
            return self._fetch_results.pop(0)
        return None

    def push_fetch(self, row: dict[str, Any] | None) -> None:
        self._fetch_results.append(row)

    def repeat_for(self, trigger: str, row: dict[str, Any] | None) -> None:
        self.repeat[trigger] = row


def make_record(**overrides: Any) -> InterventionRecord:
    base: dict[str, Any] = {
        "id": None,
        "run_id": "run-1",
        "interrupt_id": "int-1",
        "org_id": "org-1",
        "surface": "pull_request",
        "workflow_key": "docs",
        "agent_id": "delivery.github",
        "node_id": "deliver",
        "tool_name": "create_comment",
        "status": "pending",
        "reason": {"rule": "delivery:idempotency", "phase": "BEFORE_TOOL"},
        "response_message": None,
        "metadata": {},
        "idempotency_key": "req-1",
        "resolver_id": None,
        "created_at": None,
        "updated_at": None,
        "resolved_at": None,
        "expires_at": None,
    }
    base.update(overrides)
    return InterventionRecord(**base)


def row_from(record: InterventionRecord, **overrides: Any) -> dict[str, Any]:
    return {**record.model_dump(exclude_none=True), **overrides}


# ---------------------------------------------------------------------------
# SteeringAttemptsRepository.reserve
# ---------------------------------------------------------------------------


async def test_reserve_uses_atomic_upsert_with_limit_guard() -> None:
    client = FakeClient()
    client.push_fetch({"guide_count": 1})
    repo = SteeringAttemptsRepository(database=client)
    key = AttemptKey(
        run_id="run-1", agent_id="writer", node_id="write",
        phase="tool", tool_name="write_file",
    )

    reserved = await repo.reserve(key=key, limit=2)

    assert reserved is True
    sql, params = client.fetched[0]
    assert "INSERT INTO steering_attempts" in sql
    assert "ON CONFLICT" in sql
    assert "where steering_attempts.guide_count < $7" in sql.lower()
    assert params[:6] == ("run-1", "writer", "write", "tool", "write_file", 0)
    assert params[6] == 2


async def test_reserve_refuses_when_budget_exhausted() -> None:
    client = FakeClient()
    client.push_fetch(None)  # guard (guide_count >= limit) fails -> no row returned
    repo = SteeringAttemptsRepository(database=client)
    key = AttemptKey(
        run_id="run-1", agent_id="writer", node_id="write",
        phase="tool", tool_name="write_file",
    )

    reserved = await repo.reserve(key=key, limit=2)

    assert reserved is False


async def test_reserve_scopes_model_turn() -> None:
    client = FakeClient()
    client.push_fetch({"guide_count": 1})
    repo = SteeringAttemptsRepository(database=client)
    key = AttemptKey(
        run_id="run-1", agent_id="judge", node_id="judge",
        phase="model", model_turn=3,
    )

    reserved = await repo.reserve(key=key, limit=2)

    assert reserved is True
    sql, params = client.fetched[0]
    assert params[:6] == ("run-1", "judge", "judge", "model", "", 3)


# ---------------------------------------------------------------------------
# SteeringInterventionsRepository.create_pending
# ---------------------------------------------------------------------------


async def test_create_pending_inserts_pending_row() -> None:
    client = FakeClient()
    record = make_record()
    client.push_fetch(row_from(record, id="int-uuid-1"))
    repo = SteeringInterventionsRepository(database=client)

    created = await repo.create_pending(record=record)

    assert created.id == "int-uuid-1"
    assert created.status == "pending"
    sql, _ = client.fetched[0]
    assert "INSERT INTO workflow_interventions" in sql
    assert "doing nothing" not in sql  # conflicts handled by a retry lookup


async def test_create_pending_returns_existing_on_duplicate_interrupt() -> None:
    client = FakeClient()
    client.push_fetch(None)  # insert conflicted on (run_id, interrupt_id)
    existing = make_record(id="int-uuid-1")
    client.push_fetch(row_from(existing))
    repo = SteeringInterventionsRepository(database=client)

    created = await repo.create_pending(record=make_record())

    assert created.id == "int-uuid-1"
    assert len(client.fetched) == 2


async def test_create_pending_supplies_org_idempotency_unique() -> None:
    client = FakeClient()
    record = make_record()
    client.push_fetch(row_from(record, id="int-uuid-1"))
    repo = SteeringInterventionsRepository(database=client)

    await repo.create_pending(record=record)

    sql, _ = client.fetched[0]
    assert "idempotency_key" in sql
    assert "status" in sql  # defaulted to 'pending'


# ---------------------------------------------------------------------------
# SteeringInterventionsRepository.claim_response
# ---------------------------------------------------------------------------


async def test_claim_response_is_idempotent_and_single_writer() -> None:
    client = FakeClient()
    client.push_fetch(None)  # no earlier resolved row for this idempotency key
    resolved = row_from(
        make_record(),
        id="int-uuid-1",
        status="approved",
        resolver_id="approve",
        response_message="go",
        resolved_at="2026-01-01T00:00:00Z",
    )
    client.repeat_for("update workflow_interventions", resolved)
    repo = SteeringInterventionsRepository(database=client)

    first = await repo.claim_response(
        run_id="run-1", interrupt_id="int-1", org_id="org-1",
        idempotency_key="req-1", action="approve", message="go",
    )
    second = await repo.claim_response(
        run_id="run-1", interrupt_id="int-1", org_id="org-1",
        idempotency_key="req-1", action="approve", message="go",
    )

    assert first.id == second.id == "int-uuid-1"
    assert second.status == "approved"
    update_sql, _ = client.fetched[1]
    assert "status" in update_sql.lower()
    assert "update workflow_interventions" in update_sql.lower()


async def test_claim_response_rejects_different_response_after_resolution() -> None:
    client = FakeClient()
    resolved = row_from(
        make_record(),
        id="int-uuid-1",
        status="approved",
        resolver_id="approve",
        response_message="go",
        resolved_at="2026-01-01T00:00:00Z",
    )
    client.repeat_for("select", resolved)  # replay hit on the idempotency lookup
    repo = SteeringInterventionsRepository(database=client)

    with pytest.raises(InterventionConflictError):
        await repo.claim_response(
            run_id="run-1", interrupt_id="int-1", org_id="org-1",
            idempotency_key="req-1", action="deny", message="no",
        )


async def test_claim_response_mismatched_interrupt_raises() -> None:
    client = FakeClient()
    client.push_fetch(None)  # no resolved row for idempotency key
    client.push_fetch(None)  # UPDATE for interrupt-999 matched no pending row
    repo = SteeringInterventionsRepository(database=client)

    with pytest.raises(InterventionNotFoundError):
        await repo.claim_response(
            run_id="run-1", interrupt_id="int-999", org_id="org-1",
            idempotency_key="req-1", action="approve", message="go",
        )


async def test_claim_response_maps_actions() -> None:
    client = FakeClient()
    client.push_fetch(None)
    resolved = row_from(make_record(), id="int-uuid-1", status="guided")
    client.repeat_for("update workflow_interventions", resolved)

    claimed = await SteeringInterventionsRepository(database=client).claim_response(
        run_id="run-1", interrupt_id="int-1", org_id="org-1",
        idempotency_key="req-1", action="guide", message="add evidence",
    )

    assert claimed.status == "guided"


# ---------------------------------------------------------------------------
# resolve / get_pending
# ---------------------------------------------------------------------------


async def test_resolve_marks_pending_resolved() -> None:
    client = FakeClient()
    client.push_fetch(row_from(make_record(), id="int-uuid-1", status="cancelled"))
    repo = SteeringInterventionsRepository(database=client)

    resolved = await repo.resolve(intervention_id="int-uuid-1", status="cancelled")

    assert resolved.status == "cancelled"
    sql, params = client.fetched[0]
    assert "update workflow_interventions" in sql.lower()
    assert params == ("cancelled", "int-uuid-1", None)


async def test_get_pending_is_org_scoped() -> None:
    client = FakeClient()
    client.push_fetch(row_from(make_record()))
    repo = SteeringInterventionsRepository(database=client)

    found = await repo.get_pending(run_id="run-1", interrupt_id="int-1", org_id="org-1")

    assert found is not None
    sql, params = client.fetched[0]
    assert "workflow_interventions" in sql
    assert "org_id" in sql and "status = 'pending'" in sql.lower()
    assert params == ("run-1", "int-1", "org-1")


# ---------------------------------------------------------------------------
# SteeringPersistence adapter (protocol consumed by SteeringRuntime)
# ---------------------------------------------------------------------------


async def test_persistence_adapter_reserves_tool_guide_with_role_limits() -> None:
    client = FakeClient()
    client.push_fetch({"guide_count": 1})
    persistence = SteeringPersistence(
        attempts=SteeringAttemptsRepository(database=client),
        interventions=SteeringInterventionsRepository(database=client),
    )

    reserved = await persistence.reserve_tool_guide(
        run_id="run-1", agent_id="writer", node_id="write", tool_name="write_file",
    )

    assert reserved is True
    sql, params = client.fetched[0]
    assert params[:6] == ("run-1", "writer", "write", "tool", "write_file", 0)
    assert params[6] == 2  # default tool_guides_per_call


async def test_persistence_adapter_reserves_model_guide() -> None:
    client = FakeClient()
    client.push_fetch({"guide_count": 1})
    persistence = SteeringPersistence(
        attempts=SteeringAttemptsRepository(database=client),
        interventions=SteeringInterventionsRepository(database=client),
    )

    reserved = await persistence.reserve_model_guide(
        run_id="run-1", agent_id="judge", node_id="judge",
    )

    assert reserved is True
    sql, params = client.fetched[0]
    assert params[:6] == ("run-1", "judge", "judge", "model", "", 0)


async def test_persistence_adapter_claims_and_resolves_via_repositories() -> None:
    client = FakeClient()
    persistence = SteeringPersistence(
        attempts=SteeringAttemptsRepository(database=client),
        interventions=SteeringInterventionsRepository(database=client),
    )
    client.push_fetch(None)
    resolved = row_from(make_record(), id="int-uuid-1", status="approved")
    client.repeat_for("update workflow_interventions", resolved)

    claimed = await persistence.claim_response(
        run_id="run-1", interrupt_id="int-1", org_id="org-1",
        idempotency_key="req-1", action="approve", message="go",
    )

    assert claimed.id == "int-uuid-1"
    assert claimed.status == "approved"