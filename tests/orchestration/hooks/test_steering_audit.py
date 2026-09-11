"""Tests for the steering audit adapter (bounded decision writes)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

from draftly.orchestration.hooks.audit import SteeringAudit
from draftly.persistence.repositories.steering import InterventionRecord
from draftly.steering.decisions import DecisionKind, SteeringDecision, SteeringPhase


def _repo_mock() -> Mock:
    repo = Mock()
    repo.record_step = AsyncMock()
    repo.start_run = AsyncMock()
    repo.finish_run = AsyncMock()
    return repo


def _decision(*, rule: str = "policy:ok", reason: str = "ok") -> SteeringDecision:
    return SteeringDecision.guide(
        phase=SteeringPhase.BEFORE_TOOL,
        reason=reason,
        role=None,
        rule=rule,
    )


async def test_record_step_writes_bounded_steering_kind():
    repo = _repo_mock()
    sink = SteeringAudit(repo=repo)

    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=_decision(rule="argument:required", reason="read_file requires path"),
    )

    repo.record_step.assert_awaited_once()
    kwargs = repo.record_step.await_args.kwargs
    assert kwargs["run_id"] == "run-1"
    assert kwargs["agent_id"] == "agent-1"
    assert kwargs["node_id"] == "node-1"
    assert kwargs["kind"] == "steering"
    assert kwargs["name"] == "steering.guide"
    assert kwargs["seq"] == 0
    detail = kwargs["detail"]
    assert detail["phase"] == "before_tool"
    assert detail["action"] == "guide"
    assert detail["rule"] == "argument:required"
    assert "read_file requires path" in detail["reason"]


async def test_record_step_redacts_secret_shaped_detail():
    repo = _repo_mock()
    sink = SteeringAudit(repo=repo)
    decision = SteeringDecision(
        kind=DecisionKind.GUIDE,
        phase=SteeringPhase.BEFORE_TOOL,
        reason="view https://api.example.com?token=abc",
        rule="delivery:destination",
    )

    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=decision,
    )
    detail = repo.record_step.await_args.kwargs["detail"]
    assert "REDACTED" in str(detail)


async def test_record_step_with_tool_name_metadata():
    repo = _repo_mock()
    sink = SteeringAudit(repo=repo)

    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=_decision(),
        tool_name="read_file",
    )
    detail = repo.record_step.await_args.kwargs["detail"]
    assert detail["tool_name"] == "read_file"


async def test_record_step_includes_interrupt_id_for_interruption():
    repo = _repo_mock()
    sink = SteeringAudit(repo=repo)
    decision = SteeringDecision.interrupt(
        phase=SteeringPhase.BEFORE_TOOL,
        reason="destination outside run project",
        rule="delivery:destination-project",
    )
    decision = SteeringDecision(
        kind=decision.kind,
        phase=decision.phase,
        reason=decision.reason,
        rule=decision.rule,
        interrupt_id="intr-42",
    )

    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=decision,
    )
    detail = repo.record_step.await_args.kwargs["detail"]
    assert detail["interrupt_id"] == "intr-42"


async def test_record_step_without_repo_is_noop():
    sink = SteeringAudit(repo=None)
    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=_decision(),
    )


async def test_reason_bounded_to_reason_max_chars():
    repo = _repo_mock()
    sink = SteeringAudit(repo=repo, reason_max_chars=64)
    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=_decision(reason="x" * 5_000),
    )
    detail = repo.record_step.await_args.kwargs["detail"]
    assert len(detail["reason"]) == 64


async def test_payload_bounded_to_payload_max_bytes():
    repo = _repo_mock()
    sink = SteeringAudit(repo=repo, payload_max_bytes=256)

    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=_decision(reason="y" * 4_000),
    )
    import json

    detail = repo.record_step.await_args.kwargs["detail"]
    assert len(json.dumps(detail).encode()) <= 256


async def test_works_with_an_intervention_record_style_decision():
    repo = _repo_mock()
    sink = SteeringAudit(repo=repo)
    record = InterventionRecord(
        run_id="run-1",
        interrupt_id="intr-9",
        agent_id="agent-1",
        tool_name="create_comment",
    )
    decision = SteeringDecision.interrupt(
        phase=SteeringPhase.BEFORE_TOOL,
        reason=f"intervention {record.interrupt_id}",
        rule="delivery:scope",
    )
    decision = SteeringDecision(
        kind=decision.kind,
        phase=decision.phase,
        reason=decision.reason,
        rule=decision.rule,
        interrupt_id=record.interrupt_id,
    )

    await sink.record_step(
        run_id="run-1",
        agent_id="agent-1",
        node_id="node-1",
        decision=decision,
        tool_name=record.tool_name,
    )
    detail = repo.record_step.await_args.kwargs["detail"]
    assert detail["interrupt_id"] == "intr-9"
    assert detail["tool_name"] == "create_comment"