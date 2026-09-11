"""GitHub issue analyzer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import ISSUE_ANALYZER_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import ImpactAnalysis
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_issue_analyzer(
    model: Any,
    tools: list[Any] | None = None,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the GitHub issue analyzer agent."""

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            ISSUE_ANALYZER_PROMPT,
            output_model=ImpactAnalysis,
            documentation_policy="documentation_policy",
        ),
        model=model,
        tools=tools or [],
        structured_output_model=ImpactAnalysis,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "github-issue-analysis",
                    "documentation-gap-detection",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "issue_analyzer",
        node_id=node_id or "issue_analyzer",
        name="issue_analyzer",
        description="Analyzes GitHub issues for documentation gaps.",
    )
