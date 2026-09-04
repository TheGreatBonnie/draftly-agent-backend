"""Documentation researcher agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import DOC_RESEARCH_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import EvidenceBundle


def build_documentation_researcher(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation researcher agent."""

    return Agent(
        name="doc_researcher",
        system_prompt=build_prompt(
            DOC_RESEARCH_PROMPT,
            output_model=EvidenceBundle,
            support_policy="support_policy",
            documentation_policy="documentation_policy",
        ),
        model=model,
        tools=tools,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "documentation-research",
                    "documentation-gap-detection",
                )
            )
        ],
        description="Researches documentation coverage and gaps.",
    )
