"""A research agent repeating one tool call must be steered to synthesize."""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel
from strands import tool
from strands.interventions import Deny, Guide, Proceed
from strands.types.content import Message
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from draftly.agents.factory import build_draftly_agent
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole
from draftly.steering.research_budget_guard import ResearchBudgetGuard
from tests.stub_model import StubModel


@dataclass
class _ToolCall:
    """Minimal stand-in for ``BeforeToolCallEvent`` — the guard reads only
    ``tool_use``."""

    tool_use: dict[str, Any]


def _call(name: str, **args: Any) -> _ToolCall:
    return _ToolCall({"name": name, "input": args, "toolUseId": "t1"})


async def test_repeated_identical_tool_call_is_guided_to_synthesize():
    guard = ResearchBudgetGuard(max_repeats=2, max_tool_calls=10)
    guard.before_invocation(None)

    assert isinstance(guard.before_tool_call(_call("keyword_search", query="CHANGELOG.md")), Proceed)
    assert isinstance(guard.before_tool_call(_call("keyword_search", query="CHANGELOG.md")), Proceed)

    third = guard.before_tool_call(_call("keyword_search", query="CHANGELOG.md"))
    assert isinstance(third, Guide)
    assert "synthesis" in third.feedback.lower()


async def test_varied_tool_arguments_are_not_treated_as_repetition():
    guard = ResearchBudgetGuard(max_repeats=2, max_tool_calls=10)
    guard.before_invocation(None)

    for query in ("oauth", "sessions", "rbac"):
        assert isinstance(guard.before_tool_call(_call("keyword_search", query=query)), Proceed)


async def test_total_tool_call_cap_guides_to_synthesize():
    guard = ResearchBudgetGuard(max_repeats=99, max_tool_calls=3)
    guard.before_invocation(None)

    for index in range(3):
        assert isinstance(guard.before_tool_call(_call("keyword_search", query=f"q{index}")), Proceed)

    assert isinstance(guard.before_tool_call(_call("keyword_search", query="q3")), Guide)


async def test_tool_call_after_synthesis_guide_is_denied():
    guard = ResearchBudgetGuard(max_repeats=1, max_tool_calls=99)
    guard.before_invocation(None)

    guard.before_tool_call(_call("keyword_search", query="x"))
    assert isinstance(guard.before_tool_call(_call("keyword_search", query="x")), Guide)
    assert isinstance(guard.before_tool_call(_call("keyword_search", query="x")), Deny)


async def test_synthesis_tool_is_allowed_after_guide():
    """The structured-output tool is how the agent delivers its synthesis, so a
    first-time tool call must survive the post-guide deny stage."""
    guard = ResearchBudgetGuard(max_repeats=1, max_tool_calls=99)
    guard.before_invocation(None)

    guard.before_tool_call(_call("keyword_search", query="x"))
    assert isinstance(guard.before_tool_call(_call("keyword_search", query="x")), Guide)

    assert isinstance(guard.before_tool_call(_call("Coverage", gaps=[])), Proceed)


async def test_counters_reset_between_invocations():
    guard = ResearchBudgetGuard(max_repeats=1, max_tool_calls=99)
    guard.before_invocation(None)
    guard.before_tool_call(_call("keyword_search", query="x"))
    assert isinstance(guard.before_tool_call(_call("keyword_search", query="x")), Guide)

    guard.before_invocation(None)

    assert isinstance(guard.before_tool_call(_call("keyword_search", query="x")), Proceed)


class Coverage(BaseModel):
    gaps: list[str]


class LoopingModel(StubModel):
    """Replays one identical tool call until the guard steers it to synthesize."""

    def __init__(self) -> None:
        super().__init__(structured_outputs={Coverage: {"gaps": ["oauth-login"]}})
        self.tool_calls = 0

    async def stream(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        guided = "Research budget reached" in json.dumps(messages, default=str)
        if guided:
            async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield event
            return

        self.tool_calls += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "start": {"toolUse": {"toolUseId": f"loop-{self.tool_calls}", "name": "keyword_search"}}
            }
        }
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"query": "CHANGELOG.md"})}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


@tool
def keyword_search(query: str, limit: int = 10) -> str:
    """Search the documentation store."""
    del query, limit
    return "no results"


async def test_looping_research_agent_terminates_with_synthesis():
    model = LoopingModel()
    agent = build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt="Research then return Coverage.",
        model=model,
        tools=[keyword_search],
        runtime=SteeringRuntime.disabled(),
        agent_id="docs_researcher",
        node_id="research",
        structured_output_model=Coverage,
        budget=ResearchBudgetGuard(max_repeats=2, max_tool_calls=8),
    )

    result = await agent.invoke_async("Research", limits={"turns": 30})

    assert result.structured_output == Coverage(gaps=["oauth-login"])
    assert model.tool_calls <= 5
