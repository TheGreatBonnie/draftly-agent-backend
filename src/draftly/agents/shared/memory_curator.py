"""Memory curator agent: curates candidates into durable long-term knowledge."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import MEMORY_CURATOR_PROMPT
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_memory_curator(
    model: Any,
    tools: list[Any] | None = None,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the memory curation agent with its read/write toolset."""

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=MEMORY_CURATOR_PROMPT,
        model=model,
        tools=list(tools or []),
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "memory_curator",
        node_id=node_id or "memory_curator",
        name="memory_curator",
        description="Curates long-term memory candidates into durable knowledge.",
    )
