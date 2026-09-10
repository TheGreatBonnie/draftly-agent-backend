"""Documentation researcher agent.

This agent is used by the support flow; the documentation GRAPH routes through
the research swarm (agents/documentation/research_swarm.py), whose
docs_researcher builds on the same DOC_RESEARCH_PROMPT. Keep both in sync.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import DOC_RESEARCH_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import EvidenceBundle
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_documentation_researcher(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the documentation researcher agent."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
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
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "doc_researcher",
        node_id=node_id or "doc_researcher",
        name="doc_researcher",
        description="Researches documentation coverage and gaps.",
    )
