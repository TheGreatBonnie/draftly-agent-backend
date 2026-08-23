"""Telemetry: outcomes recorded around graph invocation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from draftly.models.schemas import RoutingDecision
from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner


def _decision(**overrides):
    fields: dict[str, Any] = {
        "selected_model": "alpha",
        "provider": "openrouter",
        "score": 0.9,
        "candidates_considered": 3,
        "profile": "support",
        "task_type": "support",
        "reason_codes": ("profile_support",),
        "fallback_chain": ("beta",),
    }
    fields.update(overrides)
    return RoutingDecision(**fields)


async def test_outcome_recorded_on_success():
    decision = _decision()
    context = WorkflowContext(repositories=MagicMock())
    context.routing_decision = decision
    repositories = context.repositories
    repositories.performance = MagicMock()
    repositories.performance.record_outcome = AsyncMock(return_value=None)
    repositories.performance.flush_entry = AsyncMock(return_value=None)
    repositories.routing = MagicMock()
    repositories.routing.record = AsyncMock(return_value=None)

    runner = WorkflowRunner(context)
    # Drive the recording helper directly: it is the telemetry choke point.
    await runner._record_routing_outcome(
        run_id="run-1",
        success=True,
        latency_ms=1234.0,
    )

    repositories.routing.record.assert_awaited_once()
    assert repositories.routing.record.await_args is not None
    row = repositories.routing.record.await_args.args[0]
    assert row["selected_model"] == "alpha"
    assert row["task_type"] == "support"
    assert row["success"] is True
    assert row["latency_ms"] == 1234.0
    repositories.performance.record_outcome.assert_awaited_once_with(
        task_type="support", model_name="alpha", success=True, latency_ms=1234.0,
    )


async def test_no_telemetry_without_decision():
    context = WorkflowContext(repositories=MagicMock())
    repositories = context.repositories
    repositories.routing = MagicMock()
    repositories.routing.record = AsyncMock()
    repositories.performance = MagicMock()

    runner = WorkflowRunner(context)
    await runner._record_routing_outcome(run_id="r", success=True, latency_ms=1.0)

    repositories.routing.record.assert_not_awaited()


async def test_task_type_keyed_not_profile_when_they_differ():
    """Research tasks run on the reasoning profile; aggregates must key on task type."""
    decision = _decision(profile="reasoning", task_type="research",
                         reason_codes=("profile_reasoning",))
    context = WorkflowContext(repositories=MagicMock())
    context.routing_decision = decision
    repositories = context.repositories
    repositories.performance = MagicMock()
    repositories.performance.record_outcome = AsyncMock(return_value=None)
    repositories.performance.flush_entry = AsyncMock(return_value=None)
    repositories.routing = MagicMock()
    repositories.routing.record = AsyncMock(return_value=None)

    runner = WorkflowRunner(context)
    await runner._record_routing_outcome(
        run_id="run-2",
        success=True,
        latency_ms=10.0,
    )

    assert repositories.routing.record.await_args is not None
    row = repositories.routing.record.await_args.args[0]
    assert row["task_type"] == "research"
    assert row["profile"] == "reasoning"
    repositories.performance.record_outcome.assert_awaited_once_with(
        task_type="research", model_name="alpha", success=True, latency_ms=10.0,
    )
