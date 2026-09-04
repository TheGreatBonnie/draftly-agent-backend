"""Support question analyzer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import SUPPORT_TRIAGE_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import ImpactAnalysis


def build_question_analyzer(
    model: Any,
    tools: list[Any] | None = None,
) -> Agent:
    """Build the support question analyzer agent."""

    return Agent(
        name="support_analyzer",
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
        description="Analyzes support questions for documentation gaps.",
    )
