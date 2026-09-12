"""Rollout controls: shadow vs enforcement, kill switch, judge failure modes.

Task 10: steering ships behind ``steering_enabled`` (install) and
``steering_enforcement_enabled`` (enforce). Shadow mode records the full
would-have decision and emits metrics but returns ``Proceed`` and never
persists an interruption. The global kill switch disables judge/enforcement
only and never bypasses ``ReviewGate``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from draftly.app.config import Settings
from draftly.observability.metrics import Metrics
from draftly.persistence.repositories.steering import InterventionRecord
from draftly.steering import handler as handler_module
from draftly.steering.context import RuntimeScope, SteeringRuntime, SteeringRuntimeConfig
from draftly.steering.decisions import AgentRole, DecisionKind, SteeringFailure
from draftly.steering.handler import (
    DraftlySteeringHandler,
    _JudgedSteering,
    build_steering_judge,
)
from draftly.steering.policy import policy_for

CHECKOUT = "/tmp/checkout"


class FakeAgent:
    pass


class FakeAttempts:
    def __init__(self, *, tool_allow: bool = True, model_allow: bool = True) -> None:
        self.tool_allow = tool_allow
        self.model_allow = model_allow
        self.tool_claims: list[tuple] = []
        self.model_claims: list[tuple] = []

    async def reserve_tool_guide(self, *, run_id, agent_id, node_id, tool_name):
        self.tool_claims.append((run_id, agent_id, node_id, tool_name))
        return self.tool_allow

    async def reserve_model_guide(self, *, run_id, agent_id, node_id):
        self.model_claims.append((run_id, agent_id, node_id))
        return self.model_allow


class FakeInterventions:
    def __init__(self) -> None:
        self.create_pending = AsyncMock(
            return_value=InterventionRecord(id="i-1", interrupt_id="intr-1")
        )
        self.get_pending = AsyncMock(return_value=None)


class FaultyAudit:
    def __init__(self) -> None:
        self.record_step = AsyncMock(side_effect=RuntimeError("audit down"))


def build_delivery_runtime(
    *,
    enforcement: bool,
    enabled: bool = True,
    audit=None,
    interventions: FakeInterventions | None = None,
) -> SteeringRuntime:
    if audit is None:
        audit = Mock()
        audit.record_step = AsyncMock()
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
            repo_checkout_root=CHECKOUT,
        ),
        config=SteeringRuntimeConfig(enabled=enabled, enforcement_enabled=enforcement),
        attempts=FakeAttempts(),
        audit=audit,
        interventions=interventions,
    ).for_agent(agent_id="agent-1", node_id="node-1", role=AgentRole.DELIVERY)


def build_writer_runtime(*, enforcement: bool) -> SteeringRuntime:
    audit = Mock()
    audit.record_step = AsyncMock()
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
            repo_checkout_root=CHECKOUT,
        ),
        config=SteeringRuntimeConfig(enabled=True, enforcement_enabled=enforcement),
        attempts=FakeAttempts(),
        audit=audit,
    ).for_agent(agent_id="writer-1", node_id="node-w", role=AgentRole.WRITER)


def unsafe_side_effect_tool() -> dict:
    """A delivery tool missing explicit idempotency metadata.

    The handler stamps a deterministic idempotency key onto side-effect
    tools before the policy check, so this shape proceeds once stamped.
    """
    return {
        "name": "create_comment",
        "destination_project": "project-1",
        "repo_dir": f"{CHECKOUT}/docs",
        "body": "excerpt",
    }


def metrics_registry(monkeypatch: pytest.MonkeyPatch) -> Metrics:
    registry = Metrics()
    monkeypatch.setattr(handler_module, "_metrics", registry)
    return registry


# ----------------------------------------------------------------------
# Shadow mode
# ----------------------------------------------------------------------

async def test_shadow_mode_records_would_have_decision_without_canceling_tool():
    runtime = build_delivery_runtime(enforcement=False)
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use=unsafe_side_effect_tool()
    )
    assert type(action).__name__ == "Proceed"
    runtime.audit.record_step.assert_awaited_once()
    decision = runtime.audit.record_step.await_args.kwargs["decision"]
    assert decision.kind is DecisionKind.PROCEED
    assert decision.rule == "policy:ok"


async def test_shadow_mode_never_persists_an_interruption():
    runtime = build_delivery_runtime(
        enforcement=False, interventions=FakeInterventions()
    )
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use=unsafe_side_effect_tool()
    )
    assert type(action).__name__ == "Proceed"
    runtime.interventions.create_pending.assert_not_awaited()


async def test_shadow_mode_emits_would_have_decision_metrics(monkeypatch):
    registry = metrics_registry(monkeypatch)
    runtime = build_delivery_runtime(enforcement=False)
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    await handler.steer_before_tool(
        agent=FakeAgent(), tool_use=unsafe_side_effect_tool()
    )

    counters = registry.snapshot()["counters"]
    assert counters.get("draftly_steering_shadow_decisions_total") == 1
    assert counters.get("draftly_steering_actions_proceed_total") == 1
    assert counters.get("draftly_steering_roles_delivery_total") == 1
    assert counters.get("draftly_steering_surfaces_pull_request_total") == 1
    assert "draftly_steering_interrupts_created_total" not in counters


async def test_concurrent_shadow_steers_have_no_side_effects():
    runtime = build_delivery_runtime(
        enforcement=False, interventions=FakeInterventions()
    )
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    actions = await asyncio.gather(
        *[
            handler.steer_before_tool(
                agent=FakeAgent(), tool_use=unsafe_side_effect_tool()
            )
            for _ in range(4)
        ]
    )
    assert all(type(a).__name__ == "Proceed" for a in actions)
    runtime.interventions.create_pending.assert_not_awaited()
    assert runtime.audit.record_step.call_count == 4


# ----------------------------------------------------------------------
# Enforcement mode
# ----------------------------------------------------------------------

async def test_enforcement_mode_proceeds_side_effect_once_key_stamped():
    runtime = build_delivery_runtime(
        enforcement=True, interventions=FakeInterventions()
    )
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use=unsafe_side_effect_tool()
    )
    assert type(action).__name__ == "Proceed"
    runtime.interventions.create_pending.assert_not_awaited()
    assert handler.last_interrupt_id is None


async def test_enforcement_mode_is_default_when_flags_unset():
    runtime = build_delivery_runtime(
        enforcement=True, interventions=FakeInterventions()
    )
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use=unsafe_side_effect_tool()
    )
    assert type(action).__name__ == "Proceed"
    runtime.interventions.create_pending.assert_not_awaited()


async def test_unknown_tool_is_not_gated_in_enforcement_mode():
    runtime = build_delivery_runtime(enforcement=True)
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={"name": "some_new_read_tool", "path": f"{CHECKOUT}/docs/x.md"},
    )
    assert type(action).__name__ == "Proceed"


# ----------------------------------------------------------------------
# Kill switch
# ----------------------------------------------------------------------

async def test_kill_switch_disables_steering_without_audit():
    runtime = build_delivery_runtime(enforcement=True, enabled=False)
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use=unsafe_side_effect_tool()
    )
    assert type(action).__name__ == "Proceed"
    runtime.audit.record_step.assert_not_awaited()


def test_kill_switch_does_not_disable_review_gate():
    settings = Settings(strands_steering_enabled=False, strands_review_policy="always")
    assert settings.strands.steering_enabled is False
    assert settings.strands.review_policy == "always"


# ----------------------------------------------------------------------
# Judge failure modes record fallback telemetry
# ----------------------------------------------------------------------

class StubJudgeAgent:
    """Sleeps/raises to exercise the judge boundary."""

    def __init__(self, *, raise_exc: Exception | None = None, delay: float = 0.0) -> None:
        self.raise_exc = raise_exc
        self.delay = delay

    def __call__(self, prompt, *, structured_output_model=None):
        if self.delay:
            import time

            time.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        structured = _JudgedSteering(decision="proceed", reason="judged")
        return type("AgentResult", (), {"structured_output": structured})()


async def test_judge_timeout_falls_back_and_counts_fallback(monkeypatch):
    registry = metrics_registry(monkeypatch)
    judge = build_steering_judge(StubJudgeAgent(delay=0.2), timeout_seconds=0.01)
    runtime = build_writer_runtime(enforcement=True)
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.WRITER), judge=judge
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={
            "name": "write_file",
            "path": f"{CHECKOUT}/docs/new.md",
            "content": "draft",
            "evidence": [{"id": "doc-1"}],
        },
    )
    assert type(action).__name__ == "Proceed"

    snapshot = registry.snapshot()
    assert snapshot["counters"].get("draftly_steering_judge_fallbacks_total") == 1
    assert snapshot["timings"]["draftly_steering_judge_latency_ms"]["count"] == 1


async def test_judge_provider_failure_fails_open_and_counts_fallback(monkeypatch):
    registry = metrics_registry(monkeypatch)
    judge = build_steering_judge(
        StubJudgeAgent(raise_exc=RuntimeError("provider unavailable")),
        timeout_seconds=1.0,
    )
    runtime = build_writer_runtime(enforcement=True)
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.WRITER), judge=judge
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={
            "name": "write_file",
            "path": f"{CHECKOUT}/docs/new.md",
            "content": "draft",
            "evidence": [{"id": "doc-1"}],
        },
    )
    assert type(action).__name__ == "Proceed"
    assert registry.snapshot()["counters"].get("draftly_steering_judge_fallbacks_total") == 1


async def test_guide_limit_exhaustion_counts_metric(monkeypatch):
    registry = metrics_registry(monkeypatch)
    runtime = build_delivery_runtime(
        enforcement=True, interventions=FakeInterventions()
    )
    runtime.attempts.tool_allow = False
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )
    assert type(action).__name__ == "Interrupt"
    assert registry.snapshot()["counters"].get("draftly_steering_guide_limits_total") == 1


async def test_audit_failure_fails_closed_and_counts(monkeypatch):
    registry = metrics_registry(monkeypatch)
    runtime = build_delivery_runtime(enforcement=True, audit=FaultyAudit())
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    with pytest.raises(SteeringFailure, match="audit write failed"):
        await handler.steer_before_tool(
            agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
        )
    assert registry.snapshot()["counters"].get("draftly_steering_audit_failures_total") == 1
