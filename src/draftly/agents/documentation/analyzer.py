"""Documentation impact analyzer agent (the ``impact`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import IMPACT_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import ImpactAnalysis
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_impact_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the impact analysis agent."""

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            IMPACT_PROMPT,
            output_model=ImpactAnalysis,
            documentation_policy="documentation_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=ImpactAnalysis,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "github-pr-analysis",
                    "documentation-research",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "impact",
        node_id=node_id or "impact",
        name="impact",
        description="Analyzes documentation impact and decides answer/update/create.",
    )
