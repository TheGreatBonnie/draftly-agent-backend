from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from draftly.app.agentcore.app import create_agentcore_app
from draftly.app.agentcore.routes import (
    InvocationInput,
    InvocationRequest,
    _extract_trace_id,
    _resolve_event_id,
    invocations,
)
from draftly.workflows.state import WorkflowState, WorkflowStatus


def test_settings_expose_agentcore_port() -> None:
    from draftly.app.config import get_settings

    assert hasattr(get_settings(), "agentcore_port")


def test_ping_route_is_registered() -> None:
    app = create_agentcore_app(with_lifespan=False)

    assert app.url_path_for("ping") == "/ping"


def test_ping_returns_healthy() -> None:
    client = TestClient(create_agentcore_app(with_lifespan=False))

    response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_prepare_workflows_composes_without_worker_boot() -> None:
    from draftly.app.lifecycle import DraftlyApplication

    app = DraftlyApplication(
        settings=MagicMock(),
        dependencies=MagicMock(),
        tools=object(),
    )
    start_infra = AsyncMock()
    build_agents = AsyncMock()

    with patch.object(DraftlyApplication, "_start_infrastructure", start_infra), \
         patch.object(DraftlyApplication, "_build_agents_and_workflows", build_agents):
        await app.prepare_workflows()

    assert app._started is True
    start_infra.assert_awaited_once()
    build_agents.assert_awaited_once()


# --- POST /invocations tests ---


def test_resolve_event_id_prefers_existing() -> None:
    assert _resolve_event_id({"event_id": "ev-1"}, "x" * 33) == "ev-1"


def test_resolve_event_id_uses_long_session_id() -> None:
    session = "s" * 33
    assert _resolve_event_id({}, session) == session


def test_resolve_event_id_short_session_falls_back_uuid() -> None:
    result = _resolve_event_id({}, "short")
    assert result.startswith("agentcore-")
    assert result != "short"


async def test_invocations_rejects_missing_event_type() -> None:
    request = MagicMock()
    request.app.state.draftly = MagicMock()

    with pytest.raises(HTTPException) as excinfo:
        await invocations(
            InvocationRequest(input=InvocationInput(event={})),
            request,
        )

    assert excinfo.value.status_code == 400


async def test_invocations_returns_503_when_runner_unavailable() -> None:
    request = MagicMock()
    request.app.state.draftly = MagicMock(workflows=MagicMock(runner=None))

    with pytest.raises(HTTPException) as excinfo:
        await invocations(
            InvocationRequest(
                input=InvocationInput(event={"event_id": "ev-1", "event_type": "slack_support"})
            ),
            request,
        )

    assert excinfo.value.status_code == 503


async def test_invocations_runs_workflow_and_wraps_output() -> None:
    state = WorkflowState(run_id="ev-1", event={"event_type": "slack_support"})
    state.finish(WorkflowStatus.DELIVERED)
    runner = MagicMock()
    runner.run = AsyncMock(return_value=state)
    request = MagicMock()
    request.headers = {}
    request.app.state.draftly = MagicMock(workflows=MagicMock(runner=runner))

    response = await invocations(
        InvocationRequest(
            input=InvocationInput(event={"event_id": "ev-1", "event_type": "slack_support"})
        ),
        request,
    )

    runner.run.assert_awaited_once()
    assert response == {"output": state.to_dict()}
    assert response["output"]["status"] == "delivered"


async def test_invocations_defaults_run_id_from_session_header() -> None:
    async def fake_run(event):
        return WorkflowState(run_id=event["event_id"]).finish(WorkflowStatus.SKIPPED)

    runner = MagicMock()
    runner.run = AsyncMock(side_effect=fake_run)
    request = MagicMock()
    request.headers = {"x-agentcore-session-id": "a" * 33}
    request.app.state.draftly = MagicMock(workflows=MagicMock(runner=runner))

    response = await invocations(
        InvocationRequest(input=InvocationInput(event={"event_type": "slack_support"})),
        request,
    )

    assert response["output"]["run_id"] == "a" * 33


async def test_invocations_wraps_run_failure_as_500() -> None:
    runner = MagicMock()
    runner.run = AsyncMock(side_effect=RuntimeError("boom"))
    request = MagicMock()
    request.app.state.draftly = MagicMock(workflows=MagicMock(runner=runner))

    with pytest.raises(HTTPException) as excinfo:
        await invocations(
            InvocationRequest(
                input=InvocationInput(event={"event_id": "ev-1", "event_type": "slack_support"})
            ),
            request,
        )

    assert excinfo.value.status_code == 500
    assert "boom" in excinfo.value.detail


def test_extract_trace_id_returns_traceparent() -> None:
    assert _extract_trace_id({"traceparent": "00-abc-1-01"}) == "00-abc-1-01"


def test_extract_trace_id_missing_returns_none() -> None:
    assert _extract_trace_id({}) is None


async def test_invocations_threads_trace_id_into_output() -> None:
    state = WorkflowState(run_id="ev-1", event={"event_type": "slack_support"})
    state.finish(WorkflowStatus.SKIPPED)
    runner = MagicMock()
    runner.run = AsyncMock(return_value=state)
    request = MagicMock()
    request.headers = {"traceparent": "00-abc-1-01"}
    request.app.state.draftly = MagicMock(workflows=MagicMock(runner=runner))

    response = await invocations(
        InvocationRequest(
            input=InvocationInput(event={"event_id": "ev-1", "event_type": "slack_support"})
        ),
        request,
    )

    assert response["output"]["trace_id"] == "00-abc-1-01"


async def test_invocations_omits_trace_id_when_absent() -> None:
    state = WorkflowState(run_id="ev-1", event={"event_type": "slack_support"})
    state.finish(WorkflowStatus.SKIPPED)
    runner = MagicMock()
    runner.run = AsyncMock(return_value=state)
    request = MagicMock()
    request.headers = {}
    request.app.state.draftly = MagicMock(workflows=MagicMock(runner=runner))

    response = await invocations(
        InvocationRequest(
            input=InvocationInput(event={"event_id": "ev-1", "event_type": "slack_support"})
        ),
        request,
    )

    assert "trace_id" not in response["output"]
