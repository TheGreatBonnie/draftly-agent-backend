"""GitHub issue responder agent."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import ISSUE_RESPONDER_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import AnswerDraft


def build_issue_responder(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the GitHub issue responder agent."""

    return Agent(
        name="issue_responder",
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
        description="Responds to GitHub issues with answers or doc pointers.",
    )
