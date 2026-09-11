"""Authorized steering intervention response API contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.interventions import router
from draftly.persistence.repositories.steering import (
    InterventionConflictError,
    InterventionNotFoundError,
    InterventionRecord,
)
from draftly.workflows.runner import InterventionResumeError
from draftly.workflows.state import WorkflowState, WorkflowStatus


def run_row() -> dict:
    return {
        "id": "run-1",
        "definition_id": "00000000-0000-0000-0000-000000000001",
        "org_id": "org-1",
        "source": "webhook",
        "source_event_id": "evt-1",
        "event_type": "pull_request.opened",
        "title": "PR docs",
        "repository": "acme/docs",
        "status": "pending_intervention",
        "input": {},
        "metadata": {},
    }


def record(
    *,
    status: str = "pending",
    idempotency_key: str | None = None,
    resolver_id: str | None = None,
    phase: str = "before_tool",
) -> InterventionRecord:
    return InterventionRecord(
        id="iv-1",
        run_id="run-1",
        interrupt_id="int-1",
        org_id="org-1",
        surface="pull_request",
        workflow_key="github_pr",
        agent_id="agent-1",
        node_id="doc-writer",
        tool_name="create_comment",
        status=status,
        reason={"phase": phase},
        idempotency_key=idempotency_key,
        resolver_id=resolver_id,
    )


def make_app(
    role: str = "admin",
    *,
    pending: InterventionRecord | None = None,
    resolved: InterventionRecord | None = None,
    resume_state: WorkflowState | None = None,
    resume_error: Exception | None = None,
) -> tuple[TestClient, SimpleNamespace]:
    """Standalone API app wired with mocked repos and a mocked runner.

    ``pending`` controls the pre-claim lookup; ``resolved`` controls the
    post-claim/replay record read; ``resume_state``/``resume_error`` control
    the runner's resume call.
    """
    run = run_row()
    runs = SimpleNamespace(get=AsyncMock(return_value=run))

    async def claim(
        *,
        run_id: str,
        interrupt_id: str,
        org_id: str,
        idempotency_key: str,
        action: str,
        message: str | None,
    ) -> InterventionRecord:
        if resolved is None:
            raise InterventionNotFoundError(
                f"no pending intervention for run {run_id} interrupt {interrupt_id}"
            )
        if idempotency_key != "req-1":
            raise InterventionNotFoundError(
                f"no pending intervention for run {run_id} interrupt {interrupt_id}"
            )
        if getattr(resolved, "response_message", None) != message:
            raise InterventionConflictError(
                f"intervention {resolved.id} already resolved with a different response"
            )
        return resolved

    interventions = SimpleNamespace(
        get_pending=AsyncMock(return_value=pending),
        claim_response=AsyncMock(side_effect=claim),
    )

    async def resume(
        *,
        event: dict,
        interrupt_id: str,
        response: dict,
        graph_factory: object | None = None,
    ) -> WorkflowState:
        if resume_error is not None:
            raise resume_error
        return resume_state

    runner = SimpleNamespace(resume_intervention=AsyncMock(side_effect=resume))

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_verified_token] = lambda: {
        "org_id": "org-1",
        "user_id": "user-1",
        "org_role": role,
    }
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(workflow_runs=runs, steering_interventions=interventions)
        ),
        workflows=SimpleNamespace(runner=runner),
    )
    client = TestClient(app)
    return client, SimpleNamespace(
        runs=runs, interventions=interventions, runner=runner, run=run
    )


URL = "/api/workflow-runs/run-1/interventions/int-1/respond"
PAYLOAD = {"action": "approve", "message": "go", "idempotency_key": "req-1"}


def test_respond_intervention_requires_authorized_org() -> None:
    client, repos = make_app(role="admin", pending=record())
    client.app.dependency_overrides[get_verified_token] = lambda: {
        "org_id": "different-org",
        "user_id": "user-1",
        "org_role": "admin",
    }
    repos.runs.get.return_value = None
    response = client.post(URL, json=PAYLOAD)
    assert response.status_code == 404
    repos.runner.resume_intervention.assert_not_awaited()


def test_duplicate_intervention_response_returns_existing_state() -> None:
    resolved = record(
        status="approved", idempotency_key="req-1", resolver_id="approve"
    )
    resolved.response_message = "go"
    client, repos = make_app(
        pending=record(),
        resolved=resolved,
        resume_state=WorkflowState(run_id="run-1", status=WorkflowStatus.DELIVERED),
    )
    first = client.post(URL, json=PAYLOAD)
    assert first.status_code == 200
    assert first.json()["intervention_id"] == resolved.id
    assert first.json()["status"] == "approved"
    assert first.json()["run_status"] == WorkflowStatus.DELIVERED.value

    repos.interventions.get_pending.return_value = None  # row now resolved
    second = client.post(URL, json=PAYLOAD)
    assert second.status_code == 200
    assert second.json()["intervention_id"] == first.json()["intervention_id"]
    assert repos.runner.resume_intervention.await_count == 1


def test_duplicate_claim_with_different_response_conflicts() -> None:
    pending = record()
    resolved = record(
        status="approved", idempotency_key="req-1", resolver_id="approve"
    )
    resolved.response_message = "go"
    client, repos = make_app(pending=pending, resolved=resolved)
    first = client.post(URL, json=PAYLOAD)
    assert first.status_code == 200

    repos.interventions.get_pending.return_value = None  # row now resolved
    response = client.post(
        URL, json={**PAYLOAD, "message": "no, wait", "idempotency_key": "req-1"}
    )
    assert response.status_code == 409
    assert repos.runner.resume_intervention.await_count == 1


def test_invalid_action_rejected() -> None:
    client, _ = make_app(role="admin", pending=record())
    response = client.post(URL, json={"action": "bogus", "idempotency_key": "req-1"})
    assert response.status_code == 422


def test_intervention_action_rejects_unsupported_phase() -> None:
    client, repos = make_app(pending=record(phase="after_model"))

    response = client.post(
        URL,
        json={"action": "guide", "message": "retry", "idempotency_key": "req-1"},
    )

    assert response.status_code == 422
    repos.runner.resume_intervention.assert_not_awaited()


def test_response_message_bounds_enforced() -> None:
    client, _ = make_app(role="admin", pending=record())
    response = client.post(
        URL,
        json={
            "action": "guide",
            "message": "x" * 1_001,
            "idempotency_key": "req-1",
        },
    )
    assert response.status_code == 422


def test_response_message_is_scrubbed_in_broadcast() -> None:
    resolved = record(
        status="approved",
        idempotency_key="req-1",
        resolver_id="approve",
    )
    resolved.response_message = "approved with token=sk-abcd1234EFGH5678"
    client, _ = make_app(pending=record(), resolved=resolved)
    response = client.post(
        URL,
        json={**PAYLOAD, "message": "approved with token=sk-abcd1234EFGH5678"},
    )
    assert response.status_code == 200
    assert "sk-abcd1234EFGH5678" not in response.text
    assert "[REDACTED]" in response.json()["response_message"]


def test_missing_or_expired_intervention_returns_404() -> None:
    client, repos = make_app(role="admin", pending=None, resolved=None)
    response = client.post(URL, json=PAYLOAD)
    assert response.status_code == 404
    repos.runner.resume_intervention.assert_not_awaited()


def test_resume_failure_returns_409() -> None:
    client, _ = make_app(
        role="admin",
        pending=record(),
        resume_error=InterventionResumeError("intervention int-1 is expired"),
    )
    response = client.post(URL, json=PAYLOAD)
    assert response.status_code == 409


def test_respond_requires_authentication() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                workflow_runs=SimpleNamespace(get=AsyncMock(return_value=run_row())),
                steering_interventions=SimpleNamespace(
                    get_pending=AsyncMock(return_value=record()),
                    claim_response=AsyncMock(),
                ),
            )
        ),
        workflows=SimpleNamespace(runner=SimpleNamespace(resume_intervention=AsyncMock())),
    )
    client = TestClient(app)
    response = client.post(URL, json=PAYLOAD)
    assert response.status_code == 401


def test_respond_role_member_forbidden() -> None:
    client, _ = make_app(role="member", pending=record())
    response = client.post(URL, json=PAYLOAD)
    assert response.status_code == 403
