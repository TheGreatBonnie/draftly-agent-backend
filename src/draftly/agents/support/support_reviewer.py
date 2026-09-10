"""Support reviewer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import REVIEWER_PROMPT
from draftly.agents.schemas import EvaluationResult
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_support_reviewer(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the support answer reviewer agent."""

    return build_draftly_agent(
        role=AgentRole.SUPPORT,
        system_prompt=REVIEWER_PROMPT,
        model=model,
        tools=tools,
        structured_output_model=EvaluationResult,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "support_reviewer",
        node_id=node_id or "support_reviewer",
        name="support_reviewer",
        description="Reviews support answers for accuracy and completeness.",
    )
