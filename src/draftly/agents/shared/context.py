"""Context agent: gathers an evidence bundle for the incoming event."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import CONTEXT_PROMPT, build_prompt
from draftly.agents.schemas import EvidenceBundle
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_context_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the context agent (evidence collection)."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=build_prompt(
            CONTEXT_PROMPT,
            output_model=EvidenceBundle,
            documentation_policy="documentation_policy",
            repository_rules="repository_rules",
        ),
        model=model,
        tools=tools,
        structured_output_model=EvidenceBundle,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "context",
        node_id=node_id or "context",
        name="context",
        description="Collects evidence about the event from GitHub, search, and docs.",
    )
