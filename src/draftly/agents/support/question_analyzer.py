"""Support question analyzer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import SUPPORT_TRIAGE_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import ImpactAnalysis
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_question_analyzer(
    model: Any,
    tools: list[Any] | None = None,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the support question analyzer agent."""

    return build_draftly_agent(
        role=AgentRole.SUPPORT,
        system_prompt=build_prompt(
            SUPPORT_TRIAGE_PROMPT,
            output_model=ImpactAnalysis,
            support_policy="support_policy",
        ),
        model=model,
        tools=tools or [],
        structured_output_model=ImpactAnalysis,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "support-triage",
                    "documentation-gap-detection",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "support_analyzer",
        node_id=node_id or "support_analyzer",
        name="support_analyzer",
        description="Analyzes support questions for documentation gaps.",
    )
