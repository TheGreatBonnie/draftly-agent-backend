"""Documentation impact analyzer agent (the ``impact`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import IMPACT_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import ImpactAnalysis


def build_impact_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the impact analysis agent."""

    return Agent(
        name="impact",
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
                    "documentation-gap-detection",
                    "documentation-audit",
                )
            )
        ],
        description="Analyzes documentation impact and decides answer/update/create.",
    )
