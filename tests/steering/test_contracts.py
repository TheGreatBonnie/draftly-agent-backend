import pytest

from draftly.steering.decisions import (
    AgentRole,
    DecisionKind,
    SteeringDecision,
    SteeringPhase,
)
from draftly.steering.context import (
    AgentIdentity,
    RuntimeScope,
    SteeringRuntime,
    SteeringRuntimeConfig,
)
from draftly.steering.policy import SteeringLimits
from draftly.steering.redaction import redact_value


def test_tool_decision_maps_to_strands_action():
    decision = SteeringDecision.guide(phase=SteeringPhase.BEFORE_TOOL, reason="narrow scope")
    action = decision.to_strands_action()
    assert type(action).__name__ == "Guide"
    assert action.reason == "narrow scope"


def test_model_interrupt_is_rejected():
    with pytest.raises(ValueError, match="model steering cannot interrupt"):
        SteeringDecision.interrupt(phase=SteeringPhase.AFTER_MODEL, reason="unsafe")


def test_redaction_is_bounded_and_removes_secret_keys():
    value = {"token": "secret", "nested": {"password": "pw", "ok": "x"}}
    assert redact_value(value, max_bytes=80) == {
        "token": "[REDACTED]",
        "nested": {"password": "[REDACTED]", "ok": "x"},
    }


def test_redaction_truncates_large_values_to_byte_budget():
    value = {"data": "x" * 200}
    assert redact_value(value, max_bytes=100) == {"data": "x" * 88}


def test_redaction_truncates_nested_and_bounds_overall_payload():
    import json

    value = {"items": ["y" * 200, "z" * 200], "n": [1, 2, 3]}
    redacted = redact_value(value, max_bytes=200)
    assert len(json.dumps(redacted).encode()) <= 200


def test_steering_limits_rejects_negative_values():
    with pytest.raises(ValueError):
        SteeringLimits(tool_guides_per_call=-1)
    with pytest.raises(ValueError):
        SteeringLimits(model_guides_per_turn=-1)
    with pytest.raises(ValueError):
        SteeringLimits(total_guides_per_agent=-1)


def test_steering_limits_defaults():
    limits = SteeringLimits()
    assert limits.tool_guides_per_call == 4
    assert limits.model_guides_per_turn == 2
    assert limits.total_guides_per_agent == 7


def test_agent_identity_requires_run_agent_node_role():
    with pytest.raises(TypeError):
        AgentIdentity(run_id="run", agent_id="a", node_id="n")  # missing role
    with pytest.raises(TypeError):
        AgentIdentity(agent_id="a", node_id="n", role=AgentRole.WRITER)  # missing run_id
    with pytest.raises(TypeError):
        AgentIdentity(run_id="run", node_id="n", role=AgentRole.WRITER)  # missing agent_id
    with pytest.raises(TypeError):
        AgentIdentity(run_id="run", agent_id="a", role=AgentRole.WRITER)  # missing node_id


def test_agent_identity_valid():
    identity = AgentIdentity(
        run_id="run-1",
        agent_id="writer-1",
        node_id="write",
        role=AgentRole.WRITER,
    )
    assert identity.run_id == "run-1"
    assert identity.agent_id == "writer-1"
    assert identity.node_id == "write"
    assert identity.role is AgentRole.WRITER


def test_steering_runtime_for_agent_returns_new_scope():
    parent = SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1", surface="pull_request", org_id="org-1", project_id="p-1",
        ),
        config=SteeringRuntimeConfig(enabled=False),
    )
    child = parent.for_agent(agent_id="agent-1", node_id="node-1", role=AgentRole.WRITER)
    # Child is a new object, not a mutation of parent
    assert child is not parent
    assert parent.identity is None
    assert child.identity.run_id == "run-1"
    assert child.identity.agent_id == "agent-1"
    assert child.identity.node_id == "node-1"
    assert child.identity.role is AgentRole.WRITER
    # Child shares run scope/config with the parent
    assert child.scope is parent.scope
    assert child.config is parent.config


def test_steering_runtime_disabled_is_safe_noop():
    runtime = SteeringRuntime.disabled()
    assert runtime.enabled is False
    assert runtime.scope.run_id == ""
    child = runtime.for_agent(agent_id="a", node_id="n", role=AgentRole.WRITER)
    assert child.enabled is False


def test_decision_kind_values():
    assert DecisionKind.PROCEED.value == "proceed"
    assert DecisionKind.GUIDE.value == "guide"
    assert DecisionKind.INTERRUPT.value == "interrupt"


def test_agent_role_values():
    assert AgentRole.DELIVERY.value == "delivery"
    assert AgentRole.RESEARCH.value == "research"
    assert AgentRole.WRITER.value == "writer"
    assert AgentRole.REVIEWER.value == "reviewer"
    assert AgentRole.CLASSIFIER.value == "classifier"
    assert AgentRole.RECOMMENDER.value == "recommender"
    assert AgentRole.JUDGE.value == "judge"
    assert AgentRole.SUPPORT.value == "support"