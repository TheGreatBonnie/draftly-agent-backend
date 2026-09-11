"""Documentation reviewer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import REVIEWER_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import EvaluationResult
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_reviewer_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the documentation reviewer agent."""

    return build_draftly_agent(
        role=AgentRole.REVIEWER,
        system_prompt=build_prompt(
            REVIEWER_PROMPT,
            output_model=EvaluationResult,
            evaluation_rules="evaluation_rules",
            documentation_policy="documentation_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=EvaluationResult,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "documentation-evaluation",
                    "documentation-audit",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "doc_reviewer",
        node_id=node_id or "doc_reviewer",
        name="doc_reviewer",
        description="Reviews documentation changes against policy.",
    )
