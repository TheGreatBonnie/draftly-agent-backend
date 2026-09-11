"""GitHub issue researcher agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import RESEARCH_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import EvidenceBundle
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_issue_researcher(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the GitHub issue researcher agent."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
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
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "issue_researcher",
        node_id=node_id or "issue_researcher",
        name="issue_researcher",
        description="Researches GitHub issues for context and solutions.",
    )
