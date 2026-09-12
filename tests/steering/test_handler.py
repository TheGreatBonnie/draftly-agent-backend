"""Tests for the Strands steering handler adapter."""

import hashlib
import json
import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from draftly.persistence.repositories.steering import InterventionRecord
from draftly.steering import handler as handler_module
from draftly.steering.context import RuntimeScope, SteeringRuntime, SteeringRuntimeConfig
from draftly.steering.decisions import AgentRole, SteeringDecision, SteeringFailure, SteeringPhase
from draftly.steering.handler import DraftlySteeringHandler
from draftly.steering.persistence import SteeringAuditSink
from draftly.steering.policy import FailureMode, RolePolicy, policy_for

CHECKOUT = "/tmp/checkout"


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


class RaisingPolicy(RolePolicy):
    def __init__(self, *, role: AgentRole) -> None:
        super().__init__(
            role=role,
            side_effecting=role is AgentRole.DELIVERY,
            failure_mode=(
                FailureMode.INTERRUPT if role is AgentRole.DELIVERY else FailureMode.PROCEED
            ),
        )

    async def evaluate_tool_async(self, **kwargs):
        raise SteeringFailure("policy exploded")


class FaultyAudit:
    def __init__(self) -> None:
        self.record_step = AsyncMock(side_effect=RuntimeError("audit down"))


def build_runtime(
    *,
    role: AgentRole,
    audit=None,
    interventions: FakeInterventions | None = None,
    attempted: bool = True,
    enabled: bool = True,
) -> SteeringRuntime:
    if audit is None:
        audit = Mock()
        audit.record_step = AsyncMock()
    attempts = FakeAttempts() if attempted else None
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
            repo_checkout_root=CHECKOUT,
        ),
        config=SteeringRuntimeConfig(enabled=enabled, enforcement_enabled=True),
        attempts=attempts,
        audit=audit,
        interventions=interventions,
    ).for_agent(agent_id="agent-1", node_id="node-1", role=role)


class FakeAgent:
    pass


@pytest.fixture
def runtime() -> SteeringRuntime:
    return build_runtime(role=AgentRole.DELIVERY)


@pytest.fixture
def policy() -> RolePolicy:
    return policy_for(AgentRole.DELIVERY)


async def test_before_tool_guide_returns_strands_guide_and_audits(runtime, policy):
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy)
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )
    assert type(action).__name__ == "Guide"
    runtime.audit.record_step.assert_awaited_once()


async def test_after_model_only_returns_proceed_or_guide(runtime, policy):
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy)
    action = await handler.steer_after_model(
        agent=FakeAgent(), message={"role": "assistant", "content": "draft"},
        stop_reason="end_turn",
    )
    assert type(action).__name__ in {"Proceed", "Guide"}


async def test_before_tool_proceed_still_audits(runtime, policy):
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy)
    action = await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={
            "name": "read_file",
            "path": "/tmp/checkout/docs/index.md",
        },
    )
    assert type(action).__name__ == "Proceed"
    runtime.audit.record_step.assert_awaited_once()


async def test_decision_logs_safe_context(monkeypatch, runtime, policy):
    logger = Mock()
    monkeypatch.setattr(handler_module, "logger", logger)
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy)

    await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={"name": "read_file", "path": f"{CHECKOUT}/docs/index.md"},
    )

    logger.info.assert_called_once()
    name, kwargs = logger.info.call_args.args[0], logger.info.call_args.kwargs
    assert name == "steering_decision"
    assert kwargs["run_id"] == "run-1"
    assert kwargs["agent_id"] == "agent-1"
    assert kwargs["node_id"] == "node-1"
    assert kwargs["tool_name"] == "read_file"
    assert kwargs["action"] == "proceed"


async def test_interrupt_creates_durable_intervention_before_returning():
    runtime = build_runtime(role=AgentRole.DELIVERY, interventions=FakeInterventions())
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={
            "name": "create_comment",
            "idempotency_key": "req-1",
            "destination_project": "other-project",
            "repo_dir": f"{CHECKOUT}/docs",
            "body": "excerpt",
        },
    )
    assert type(action).__name__ == "Interrupt"
    runtime.interventions.create_pending.assert_awaited_once()

    record = runtime.interventions.create_pending.await_args.kwargs["record"]
    assert record.status == "pending"
    assert record.tool_name == "create_comment"
    assert record.interrupt_id

    decision = runtime.audit.record_step.await_args.kwargs["decision"]
    assert decision.interrupt_id == record.interrupt_id
    assert handler.last_interrupt_id == record.interrupt_id


async def test_active_audit_sink_includes_policy_and_attempt_context():
    repo = Mock()
    repo.record_step = AsyncMock()
    runtime = build_runtime(role=AgentRole.WRITER, audit=SteeringAuditSink(repo))
    runtime.config = SteeringRuntimeConfig(
        enabled=True,
        enforcement_enabled=True,
        policy_version="v9",
        tool_guides_per_call=1,
        model_guides_per_turn=2,
        total_guides_per_agent=3,
    )
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.WRITER))

    await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={"name": "read_file", "path": f"{CHECKOUT}/docs/index.md"},
    )

    detail = repo.record_step.await_args.kwargs["detail"]
    assert detail["schema_version"] == "1"
    assert detail["policy_version"] == "v9"
    assert detail["attempt_summary"] == {
        "tool_guides_per_call": 1,
        "model_guides_per_turn": 2,
        "total_guides_per_agent": 3,
    }
    assert detail["decision_source"] == "deterministic"


async def test_policy_exception_fails_closed_for_side_effecting_role():
    runtime = build_runtime(role=AgentRole.DELIVERY)
    handler = DraftlySteeringHandler(runtime=runtime, policy=RaisingPolicy(role=AgentRole.DELIVERY))
    with pytest.raises(SteeringFailure, match="policy exploded"):
        await handler.steer_before_tool(agent=FakeAgent(), tool_use={"name": "x"})


async def test_policy_exception_fails_open_for_read_only_role():
    runtime = build_runtime(role=AgentRole.RESEARCH)
    handler = DraftlySteeringHandler(runtime=runtime, policy=RaisingPolicy(role=AgentRole.RESEARCH))
    action = await handler.steer_before_tool(agent=FakeAgent(), tool_use={"name": "x"})
    assert type(action).__name__ == "Proceed"


async def test_audit_failure_fails_closed_for_side_effecting_role():
    runtime = build_runtime(role=AgentRole.DELIVERY, audit=FaultyAudit())
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.DELIVERY))
    with pytest.raises(SteeringFailure, match="audit write failed"):
        await handler.steer_before_tool(
            agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
        )


async def test_audit_failure_fails_open_for_read_only_role():
    runtime = build_runtime(role=AgentRole.RESEARCH, audit=FaultyAudit())
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.RESEARCH))
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )
    assert type(action).__name__ == "Proceed"


async def test_policy_failure_logs_warning_for_read_only(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(handler_module, "logger", logger)
    runtime = build_runtime(role=AgentRole.RESEARCH)
    handler = DraftlySteeringHandler(runtime=runtime, policy=RaisingPolicy(role=AgentRole.RESEARCH))

    action = await handler.steer_before_tool(agent=FakeAgent(), tool_use={"name": "x"})

    assert type(action).__name__ == "Proceed"
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == "steering_policy_unavailable"
    assert logger.warning.call_args.kwargs["role"] == "research"


async def test_audit_failure_logs_warning_for_read_only(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(handler_module, "logger", logger)
    runtime = build_runtime(role=AgentRole.RESEARCH, audit=FaultyAudit())
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.RESEARCH))

    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )

    assert type(action).__name__ == "Proceed"
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == "steering_audit_unavailable"
    assert logger.warning.call_args.kwargs["role"] == "research"


async def test_non_durable_interrupt_logs_warning_for_read_only(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(handler_module, "logger", logger)
    runtime = build_runtime(role=AgentRole.WRITER, interventions=None)
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.WRITER))
    decision = SteeringDecision.interrupt(
        phase=SteeringPhase.BEFORE_TOOL,
        reason="should be durable",
        role=AgentRole.WRITER,
        rule="delivery:scope",
    )

    returned = await handler._persist_intervention(decision, tool_name="create_comment")

    assert returned is decision
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == "steering_intervention_not_persisted"


async def test_missing_intervention_sink_does_not_return_untracked_interrupt():
    runtime = build_runtime(role=AgentRole.DELIVERY, interventions=None)
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.DELIVERY))

    with pytest.raises(SteeringFailure, match="intervention persistence is unavailable"):
        await handler.steer_before_tool(
            agent=FakeAgent(),
            tool_use={
                "name": "create_comment",
                "idempotency_key": "req-1",
                "destination_project": "other-project",
                "repo_dir": f"{CHECKOUT}/docs",
                "body": "excerpt",
            },
        )


async def test_guide_limit_exhaustion_terminal_interrupt_for_side_effecting():
    runtime = build_runtime(
        role=AgentRole.DELIVERY,
        interventions=FakeInterventions(),
    )
    runtime.attempts.tool_allow = False
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )
    assert type(action).__name__ == "Interrupt"
    runtime.interventions.create_pending.assert_awaited_once()


async def test_guide_limit_exhaustion_fails_open_for_read_only_role():
    runtime = build_runtime(role=AgentRole.RESEARCH)
    runtime.attempts.tool_allow = False
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.RESEARCH))
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )
    assert type(action).__name__ == "Proceed"


async def test_disabled_runtime_returns_proceed_without_audit(policy):
    runtime = build_runtime(role=AgentRole.DELIVERY, enabled=False)
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy)
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )
    assert type(action).__name__ == "Proceed"
    runtime.audit.record_step.assert_not_awaited()


async def test_returned_guide_reason_is_bounded(runtime, policy):
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy)
    action = await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={"name": "read_file", "path": "x" * 5_000},
    )
    assert type(action).__name__ == "Guide"
    assert len(action.reason) <= runtime.config.reason_max_chars


async def test_judge_cannot_override_process_decision(runtime, policy):
    async def judge(decision):
        return decision

    runtime.audit.record_step = AsyncMock()
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy, judge=judge)
    action = await handler.steer_before_tool(
        agent=FakeAgent(), tool_use={"name": "read_file", "path": "bad"},
    )
    assert type(action).__name__ == "Guide"


async def test_interrupt_id_matches_strands_tool_interrupt_scheme():
    runtime = build_runtime(role=AgentRole.DELIVERY, interventions=FakeInterventions())
    handler = DraftlySteeringHandler(
        runtime=runtime, policy=policy_for(AgentRole.DELIVERY)
    )
    action = await handler.steer_before_tool(
        agent=FakeAgent(),
        tool_use={
            "name": "create_comment",
            "toolUseId": "tool-42",
            "idempotency_key": "req-1",
            "destination_project": "other-project",
            "repo_dir": f"{CHECKOUT}/docs",
            "body": "excerpt",
        },
    )
    assert type(action).__name__ == "Interrupt"
    record = runtime.interventions.create_pending.await_args.kwargs["record"]
    assert record.interrupt_id.startswith("v1:before_tool_call:tool-42:")
    assert record.interrupt_id.endswith(
        str(uuid.uuid5(uuid.NAMESPACE_OID, "steering_input_create_comment"))
    )
    assert handler.last_interrupt_id == record.interrupt_id
    assert (
        runtime.audit.record_step.await_args.kwargs["decision"].interrupt_id
        == record.interrupt_id
    )


class ProceedPolicy(RolePolicy):
    """Minimal DELIVERY policy that always allows the tool."""

    def __init__(self) -> None:
        super().__init__(
            role=AgentRole.DELIVERY,
            side_effecting=True,
            failure_mode=FailureMode.INTERRUPT,
            side_effect_tools=frozenset({"create_comment"}),
        )

    async def evaluate_tool_async(self, **kwargs):
        return SteeringDecision.proceed(
            phase=SteeringPhase.BEFORE_TOOL,
            reason="allowed",
            role=AgentRole.DELIVERY,
        )


_IDEM_RESERVED = {"name", "toolUseId", "tool_use_id", "metadata", "idempotency_key"}


def _idem_payload(tool_use: dict) -> str:
    args = {k: v for k, v in tool_use.items() if k not in _IDEM_RESERVED}
    return json.dumps(args, sort_keys=True, default=str)


async def test_idempotency_key_injected_on_side_effect_tool():
    runtime = build_runtime(role=AgentRole.DELIVERY)
    handler = DraftlySteeringHandler(runtime=runtime, policy=ProceedPolicy())

    tool_use = {
        "name": "create_comment",
        "destination_project": "project-1",
        "repo_dir": f"{CHECKOUT}/docs",
        "body": "excerpt",
    }
    action = await handler.steer_before_tool(agent=FakeAgent(), tool_use=tool_use)

    assert type(action).__name__ == "Proceed"
    key = (tool_use.get("metadata") or {}).get("idempotency_key")
    assert isinstance(key, str) and len(key) == 64

    expected = hashlib.sha256(
        f"org-1|run-1|create_comment|{_idem_payload(tool_use)}".encode()
    ).hexdigest()
    assert key == expected


async def test_identical_calls_produce_deterministic_key():
    runtime = build_runtime(role=AgentRole.DELIVERY)
    handler = DraftlySteeringHandler(runtime=runtime, policy=ProceedPolicy())

    keys = []
    for _ in range(3):
        tool_use = {
            "name": "create_comment",
            "destination_project": "project-1",
            "repo_dir": f"{CHECKOUT}/docs",
            "body": "excerpt",
        }
        await handler.steer_before_tool(agent=FakeAgent(), tool_use=tool_use)
        keys.append((tool_use.get("metadata") or {}).get("idempotency_key"))
    assert len(set(keys)) == 1


async def test_differs_when_input_changes():
    runtime = build_runtime(role=AgentRole.DELIVERY)
    handler = DraftlySteeringHandler(runtime=runtime, policy=ProceedPolicy())

    keys = []
    for body in ("excerpt", "different body"):
        tool_use = {
            "name": "create_comment",
            "destination_project": "project-1",
            "repo_dir": f"{CHECKOUT}/docs",
            "body": body,
        }
        await handler.steer_before_tool(agent=FakeAgent(), tool_use=tool_use)
        keys.append((tool_use.get("metadata") or {}).get("idempotency_key"))
    assert keys[0] != keys[1]


async def test_read_only_tool_not_injected():
    runtime = build_runtime(role=AgentRole.WRITER)
    handler = DraftlySteeringHandler(runtime=runtime, policy=policy_for(AgentRole.WRITER))

    tool_use = {"name": "read_file", "path": f"{CHECKOUT}/docs/index.md"}
    await handler.steer_before_tool(agent=FakeAgent(), tool_use=tool_use)
    meta = tool_use.get("metadata") or {}
    assert "idempotency_key" not in meta
