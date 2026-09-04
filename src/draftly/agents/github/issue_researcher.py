"""GitHub issue researcher agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import RESEARCH_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import EvidenceBundle


def build_issue_researcher(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the GitHub issue researcher agent."""

    return Agent(
        name="issue_researcher",
        system_prompt=build_prompt(
            RESEARCH_PROMPT,
            output_model=EvidenceBundle,
            support_policy="support_policy",
            documentation_policy="documentation_policy",
        ),
        model=model,
        tools=tools,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "github-issue-analysis",
                    "repository-analysis",
                )
            )
        ],
        description="Researches GitHub issues for context and solutions.",
    )
