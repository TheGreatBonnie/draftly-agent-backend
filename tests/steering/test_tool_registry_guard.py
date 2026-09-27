"""Unknown model-selected tools must not spin through the agent loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

import pytest
from pydantic import BaseModel
from strands.types.content import Message
from strands.types.exceptions import EventLoopException
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from draftly.agents.factory import build_draftly_agent
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole
from tests.stub_model import StubModel


class Result(BaseModel):
    value: str


class InventedToolModel(StubModel):
    def __init__(self, invented_name: str, *, recover: bool = False) -> None:
        super().__init__(structured_outputs={Result: {"value": "ok"}})
        self.invented_name = invented_name
        self.recover = recover
        self.calls = 0

    async def stream(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.calls += 1
        if self.recover and self.calls > 1:
            async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield event
            return
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "start": {"toolUse": {"toolUseId": f"bad-{self.calls}", "name": self.invented_name}}
            }
        }
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({})}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


@pytest.mark.parametrize(
    ("role", "invented_name"),
    [
        (AgentRole.RESEARCH, "read"),
        (AgentRole.WRITER, "impact_analysis"),
        (AgentRole.WRITER, "github_get_file"),
    ],
)
async def test_repeated_invented_tool_fails_agent_invocation(role, invented_name):
    model = InventedToolModel(invented_name)
    agent = build_draftly_agent(
        role=role,
        system_prompt="Use available tools and return Result.",
        model=model,
        tools=[],
        runtime=SteeringRuntime.disabled(),
        agent_id=f"test-{role.value}",
        node_id="test",
        structured_output_model=Result,
    )

    with pytest.raises(EventLoopException, match="unregistered tool"):
        await agent.invoke_async("Produce Result", limits={"turns": 8})

    assert model.calls == 3


async def test_invented_tool_can_recover_to_registered_structured_output():
    model = InventedToolModel("impact_analysis", recover=True)
    agent = build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt="Return Result.",
        model=model,
        tools=[],
        runtime=SteeringRuntime.disabled(),
        agent_id="test-impact",
        node_id="impact",
        structured_output_model=Result,
    )

    result = await agent.invoke_async("Produce Result", limits={"turns": 8})

    assert result.structured_output == Result(value="ok")
    assert model.calls == 2
