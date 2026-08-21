"""GitHub issue responder agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import ISSUE_RESPONDER_PROMPT
from draftly.agents.schemas import AnswerDraft


def build_issue_responder(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the GitHub issue responder agent."""

    return Agent(
        name="issue_responder",
        system_prompt=ISSUE_RESPONDER_PROMPT,
        model=model,
        tools=tools,
        structured_output_model=AnswerDraft,
        description="Responds to GitHub issues with answers or doc pointers.",
    )
