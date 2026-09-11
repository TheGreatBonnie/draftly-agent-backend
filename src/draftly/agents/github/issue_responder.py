"""GitHub issue responder agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import ISSUE_RESPONDER_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import AnswerDraft
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_issue_responder(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the GitHub issue responder agent."""

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            ISSUE_RESPONDER_PROMPT,
            output_model=AnswerDraft,
            support_policy="support_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=AnswerDraft,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "github-delivery",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "issue_responder",
        node_id=node_id or "issue_responder",
        name="issue_responder",
        description="Responds to GitHub issues with answers or doc pointers.",
    )
