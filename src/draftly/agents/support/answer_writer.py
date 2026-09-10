"""Support answer writer agent (the ``answer`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import ANSWER_WRITER_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import AnswerDraft
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_answer_writer(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the support answer writer agent."""

    return build_draftly_agent(
        role=AgentRole.SUPPORT,
        system_prompt=build_prompt(
            ANSWER_WRITER_PROMPT,
            output_model=AnswerDraft,
            support_policy="support_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=AnswerDraft,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "support-answering",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "support_writer",
        node_id=node_id or "support_writer",
        name="support_writer",
        description="Writes answers to support questions.",
    )
