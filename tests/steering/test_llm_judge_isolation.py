"""Isolated optional LLM judge: construction boundary and runtime adapter."""

from __future__ import annotations

import time

import pytest

from draftly.steering.context import RuntimeScope, SteeringRuntime, SteeringRuntimeConfig
from draftly.steering.decisions import (
    AgentRole,
    DecisionKind,
    SteeringDecision,
    SteeringFailure,
    SteeringPhase,
)
from draftly.steering.handler import (
    DEFAULT_STEERING_JUDGE_PROMPT,
    DraftlySteeringHandler,
    _JudgedSteering,
    build_isolated_judge,
    build_steering_judge,
)
from draftly.steering.policy import FailureMode, RolePolicy, policy_for


class StubModel:
    stateful = False


class StubJudgeAgent:
    """Scripts judge responses; timeouts/invalid schemas encoded as entries."""

    def __init__(self, *steps: object, delay: float = 0.0) -> None:
        self.prompts: list[str] = []
        self._steps = list(steps)
        self.delay = delay

    def __call__(self, prompt, *, structured_output_model=None):
        self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        step = self._steps.pop(0) if self._steps else "proceed"
        if isinstance(step, Exception):
            raise step
        if isinstance(step, tuple):
            decision, structured = step
        else:
            decision = step
            structured = None
        if structured is None:
            structured = _JudgedSteering(decision=decision, reason="judged")
        return type("AgentResult", (), {"structured_output": structured})()


def build_writer_runtime() -> SteeringRuntime:
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
        ),
        config=SteeringRuntimeConfig(enabled=True, enforcement_enabled=True, llm_enabled=True),
    ).for_agent(agent_id="agent-1", node_id="node-1", role=AgentRole.WRITER)


def build_interrupt_prone_policy() -> RolePolicy:
    """A judge-enabled policy that can still deterministically interrupt."""
    return RolePolicy(
        role=AgentRole.WRITER,
        policy_version="v1",
        side_effecting=True,
        failure_mode=FailureMode.INTERRUPT,
        side_effect_tools=frozenset({"create_comment"}),
        required_text_args={"create_comment": ("body",)},
        judge_enabled=True,
    )


class TestIsolatedJudgeConstruction:
    def test_llm_judge_has_no_tools_plugins_or_steering(self) -> None:
        judge = build_isolated_judge(system_prompt="judge", model=StubModel())
        assert judge.tools == []
        assert judge.plugins == []
        assert not any(isinstance(item, DraftlySteeringHandler) for item in judge.plugins)

    def test_judge_has_dedicated_system_prompt(self) -> None:
        judge = build_isolated_judge(
            system_prompt=DEFAULT_STEERING_JUDGE_PROMPT, model=StubModel()
        )
        assert judge.system_prompt == DEFAULT_STEERING_JUDGE_PROMPT


class TestJudgeAdapter:
    async def test_judge_refines_proceed_to_guide(self) -> None:
        judge_agent = StubJudgeAgent("guide")
        judge = build_steering_judge(judge_agent, timeout_seconds=1.0)
        decision = await policy_for(AgentRole.WRITER).evaluate_tool_async(
            runtime=build_writer_runtime(),
            tool_name="write_file",
            tool_use={
                "path": "/tmp/checkout/docs/new.md",
                "content": "draft",
                "evidence": [{"source": "get_issue", "notes": "motivation"}],
            },
            judge=judge,
        )
        assert decision.kind is DecisionKind.GUIDE
        assert decision.rule == "judge:guide"
        assert decision.reason == "judged"

    async def test_judge_interrupt_after_model_is_downgraded_to_guide(self) -> None:
        judge_agent = StubJudgeAgent("interrupt")
        judge = build_steering_judge(judge_agent, timeout_seconds=1.0)
        decision = await policy_for(AgentRole.WRITER).evaluate_model_async(
            runtime=build_writer_runtime(),
            message={"role": "assistant", "content": "draft complete"},
            stop_reason="end_turn",
            judge=judge,
        )
        assert decision.kind is DecisionKind.GUIDE
        assert decision.phase is SteeringPhase.AFTER_MODEL

    async def test_judge_timeout_raises_steering_failure(self) -> None:
        judge_agent = StubJudgeAgent(delay=0.5)
        judge = build_steering_judge(judge_agent, timeout_seconds=0.05)
        base = SteeringDecision.proceed(
            phase=SteeringPhase.BEFORE_TOOL,
            reason="deterministic policy ok",
            role=AgentRole.WRITER,
            rule="policy:ok",
        )
        with pytest.raises(SteeringFailure):
            await judge(decision=base)

    async def test_judge_timeout_falls_back_to_deterministic(self) -> None:
        judge_agent = StubJudgeAgent(delay=0.5)
        judge = build_steering_judge(judge_agent, timeout_seconds=0.05)
        decision = await policy_for(AgentRole.WRITER).evaluate_tool_async(
            runtime=build_writer_runtime(),
            tool_name="write_file",
            tool_use={
                "path": "/tmp/checkout/docs/new.md",
                "content": "draft",
                "evidence": [{"source": "get_issue", "notes": "motivation"}],
            },
            judge=judge,
        )
        assert decision.kind is DecisionKind.PROCEED

    async def test_invalid_judge_schema_falls_back_to_deterministic(self) -> None:
        judge_agent = StubJudgeAgent(("proceed", "not-a-structured-output"))
        judge = build_steering_judge(judge_agent, timeout_seconds=1.0)
        decision = await policy_for(AgentRole.WRITER).evaluate_tool_async(
            runtime=build_writer_runtime(),
            tool_name="write_file",
            tool_use={
                "path": "/tmp/checkout/docs/new.md",
                "content": "draft",
                "evidence": [{"source": "get_issue", "notes": "motivation"}],
            },
            judge=judge,
        )
        assert decision.kind is DecisionKind.PROCEED


class TestJudgePrecedence:
    async def test_judge_cannot_override_deterministic_interrupt(self) -> None:
        judge_agent = StubJudgeAgent("proceed")
        judge = build_steering_judge(judge_agent, timeout_seconds=1.0)
        decision = await build_interrupt_prone_policy().evaluate_tool_async(
            runtime=build_writer_runtime(),
            tool_name="create_comment",
            tool_use={"name": "create_comment", "body": "hi"},
            judge=judge,
        )
        assert decision.kind is DecisionKind.INTERRUPT
        assert decision.rule == "delivery:idempotency"

    async def test_judge_cross_phase_refinement_is_discarded(self) -> None:
        async def cross_phase_judge(*, decision):
            return SteeringDecision.guide(
                phase=SteeringPhase.AFTER_MODEL,
                reason="judge switched phase",
                role=AgentRole.WRITER,
                rule="judge:guide",
            )

        decision = await policy_for(AgentRole.WRITER).evaluate_tool_async(
            runtime=build_writer_runtime(),
            tool_name="write_file",
            tool_use={
                "path": "/tmp/checkout/docs/new.md",
                "content": "draft",
                "evidence": [{"source": "get_issue", "notes": "motivation"}],
            },
            judge=cross_phase_judge,
        )
        assert decision.phase is SteeringPhase.BEFORE_TOOL
        assert decision.rule == "policy:ok"
