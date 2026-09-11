"""Root Draftly agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import CLASSIFIER_PROMPT
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def create_draftly_agent(
    model: Any,
    tools: list[Any] | None = None,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the root Draftly agent."""

    return build_draftly_agent(
        role=AgentRole.CLASSIFIER,
        system_prompt=(
            "You are Draftly, an autonomous documentation-engineering agent. " + CLASSIFIER_PROMPT
        ),
        model=model,
        tools=tools or [],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "draftly",
        node_id=node_id or "draftly",
        name="draftly",
        description="Root Draftly documentation-intelligence agent.",
    )
