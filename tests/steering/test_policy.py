"""Tests for versioned role policies and deterministic steering checks."""

from unittest.mock import AsyncMock

import pytest

from draftly.steering.context import RuntimeScope, SteeringRuntime, SteeringRuntimeConfig
from draftly.steering.decisions import AgentRole, DecisionKind, SteeringPhase
from draftly.steering.policy import FailureMode, RolePolicy, policy_for
from draftly.steering import policy as policy_module

CHECKOUT = "/tmp/checkout"


class FakeAttempts:
    def __init__(self) -> None:
        self.tool_claims: list[tuple] = []
        self.model_claims: list[tuple] = []
        self.tool_allow = True
        self.model_allow = True

    async def reserve_tool_guide(self, *, run_id, agent_id, node_id, tool_name):
        self.tool_claims.append((run_id, agent_id, node_id, tool_name))
        return self.tool_allow

    async def reserve_model_guide(self, *, run_id, agent_id, node_id):
        self.model_claims.append((run_id, agent_id, node_id))
        return self.model_allow


@pytest.fixture
def runtime() -> SteeringRuntime:
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
            repo_checkout_root=CHECKOUT,
        ),
        config=SteeringRuntimeConfig(enabled=True, enforcement_enabled=True),
        attempts=FakeAttempts(),
    ).for_agent(agent_id="agent-1", node_id="node-1", role=AgentRole.DELIVERY)


@pytest.fixture
def tool_use() -> dict:
    return {
        "idempotency_key": "req-1",
        "destination_project": "project-1",
        "repo_dir": f"{CHECKOUT}/docs",
        "path": f"{CHECKOUT}/docs/index.md",
        "body": "validated excerpt",
    }


def test_delivery_policy_interrupts_destination_mismatch(runtime, tool_use):
    decision = policy_for(AgentRole.DELIVERY).evaluate_tool(
        runtime=runtime,
        tool_name="create_comment",
        tool_use={**tool_use, "destination_project": "other-project"},
    )
    assert decision.kind is DecisionKind.INTERRUPT


def test_delivery_policy_interrupts_missing_idempotency(runtime, tool_use):
    decision = policy_for(AgentRole.DELIVERY).evaluate_tool(
        runtime=runtime,
        tool_name="create_comment",
        tool_use={k: v for k, v in tool_use.items() if k != "idempotency_key"},
    )
    assert decision.kind is DecisionKind.INTERRUPT


def test_delivery_policy_proceeds_on_valid_side_effect(runtime, tool_use):
    decision = policy_for(AgentRole.DELIVERY).evaluate_tool(
        runtime=runtime,
        tool_name="create_comment",
        tool_use=tool_use,
    )
    assert decision.kind is DecisionKind.PROCEED


def test_research_policy_guides_out_of_scope_read(runtime, tool_use):
    policy = policy_for(AgentRole.RESEARCH)
    decision = policy.evaluate_tool(
        runtime=runtime,
        tool_name="read_file",
        tool_use={**tool_use, "path": "/outside/checkout/secret.txt"},
    )
    assert decision.kind is DecisionKind.GUIDE


def test_research_policy_proceeds_on_in_scope_read(runtime, tool_use):
    decision = policy_for(AgentRole.RESEARCH).evaluate_tool(
        runtime=runtime,
        tool_name="read_file",
        tool_use={**tool_use, "path": f"{CHECKOUT}/docs/index.md"},
    )
    assert decision.kind is DecisionKind.PROCEED


async def test_guide_limit_exhaustion_is_fail_closed_for_delivery(runtime, tool_use):
    runtime.attempts.reserve_tool_guide = AsyncMock(return_value=False)
    decision = await policy_for(AgentRole.DELIVERY).evaluate_tool_async(
        runtime=runtime,
        tool_name="create_comment",
        tool_use={**tool_use, "body": ""},  # empty body is an invalid argument
    )
    assert decision.kind is DecisionKind.INTERRUPT


async def test_guide_reservation_is_recorded_for_agent_identity(runtime, tool_use):
    policy = policy_for(AgentRole.WRITER)
    decision = await policy.evaluate_tool_async(
        runtime=runtime,
        tool_name="write_file",
        tool_use={**tool_use, "content": ""},  # missing evidence -> guide
    )
    assert decision.kind is DecisionKind.GUIDE
    assert runtime.attempts.tool_claims == [("run-1", "agent-1", "node-1", "write_file")]


def test_missing_evidence_guides_writer():
    runtime = _readonly_runtime(AgentRole.WRITER)
    decision = policy_for(AgentRole.WRITER).evaluate_tool(
        runtime=runtime,
        tool_name="write_file",
        tool_use={"path": f"{CHECKOUT}/docs/new.md", "content": "draft"},
    )
    assert decision.kind is DecisionKind.GUIDE


def test_writer_proceeds_when_evidence_present():
    runtime = _readonly_runtime(AgentRole.WRITER)
    decision = policy_for(AgentRole.WRITER).evaluate_tool(
        runtime=runtime,
        tool_name="write_file",
        tool_use={
            "path": f"{CHECKOUT}/docs/new.md",
            "content": "draft",
            "evidence": [{"source": "get_issue", "notes": "motivation"}],
        },
    )
    assert decision.kind is DecisionKind.PROCEED


def test_model_guide_when_content_filtered():
    runtime = _readonly_runtime(AgentRole.WRITER)
    decision = policy_for(AgentRole.WRITER).evaluate_model(
        runtime=runtime,
        message={"role": "assistant", "content": ""},
        stop_reason="content_filtered",
    )
    assert decision.kind is DecisionKind.GUIDE


def test_model_proceeds_on_normal_turn():
    runtime = _readonly_runtime(AgentRole.WRITER)
    decision = policy_for(AgentRole.WRITER).evaluate_model(
        runtime=runtime,
        message={"role": "assistant", "content": "draft complete"},
        stop_reason="end_turn",
    )
    assert decision.kind is DecisionKind.PROCEED


async def test_model_guide_limit_exhausted_uses_role_failure_mode():
    runtime = _readonly_runtime(AgentRole.WRITER)
    runtime.attempts.reserve_model_guide = AsyncMock(return_value=False)
    decision = await policy_for(AgentRole.WRITER).evaluate_model_async(
        runtime=runtime,
        message={"role": "assistant", "content": ""},
        stop_reason="content_filtered",
    )
    assert decision.kind is DecisionKind.PROCEED  # read-only role fails open


async def test_judge_unavailable_falls_back_to_deterministic(runtime, tool_use):
    async def broken_judge(**kwargs):
        raise RuntimeError("judge unavailable")

    decision = await policy_for(AgentRole.DELIVERY).evaluate_tool_async(
        runtime=runtime,
        tool_name="create_comment",
        tool_use=tool_use,
        judge=broken_judge,
    )
    assert decision.kind is DecisionKind.PROCEED


async def test_judge_failure_logs_warning_and_falls_back(monkeypatch):
    from unittest.mock import Mock

    logger = Mock()
    monkeypatch.setattr(policy_module, "logger", logger, raising=False)

    async def broken_judge(**kwargs):
        raise RuntimeError("judge unavailable")

    decision = await policy_for(AgentRole.WRITER).evaluate_tool_async(
        runtime=_readonly_runtime(AgentRole.WRITER),
        tool_name="read_file",
        tool_use={"name": "read_file", "path": f"{CHECKOUT}/docs/index.md"},
        judge=broken_judge,
    )

    assert decision.kind is DecisionKind.PROCEED
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == "steering_judge_fallback"
    assert logger.warning.call_args.kwargs["phase"] == "before_tool"


def test_policy_for_requires_registered_role():
    with pytest.raises(KeyError):
        policy_for("does-not-exist")  # type: ignore[arg-type]


def test_delivery_policy_is_side_effecting_with_interrupt_failure():
    policy = policy_for(AgentRole.DELIVERY)
    assert policy.side_effecting is True
    assert policy.failure_mode is FailureMode.INTERRUPT


def test_research_policy_is_read_only_with_open_failure():
    policy = policy_for(AgentRole.RESEARCH)
    assert policy.side_effecting is False
    assert policy.failure_mode is FailureMode.PROCEED


def _readonly_runtime(role: AgentRole) -> SteeringRuntime:
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-2",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
            repo_checkout_root=CHECKOUT,
        ),
        config=SteeringRuntimeConfig(enabled=True, enforcement_enabled=True),
        attempts=FakeAttempts(),
    ).for_agent(agent_id="agent-2", node_id="node-2", role=role) or None