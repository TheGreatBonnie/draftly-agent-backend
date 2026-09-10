"""Documentation writer agent (``update`` / ``create`` nodes)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import WRITER_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import DocChangePlan
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_writer_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the documentation writer agent."""

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            WRITER_PROMPT,
            output_model=DocChangePlan,
            documentation_policy="documentation_policy",
            writing_style="writing_style",
            repository_rules="repository_rules",
            security_rules="security_rules",
        ),
        model=model,
        tools=tools,
        structured_output_model=DocChangePlan,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "documentation-update",
                    "documentation-generation",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "doc_writer",
        node_id=node_id or "doc_writer",
        name="doc_writer",
        description="Writes documentation change plans (create/update).",
    )
