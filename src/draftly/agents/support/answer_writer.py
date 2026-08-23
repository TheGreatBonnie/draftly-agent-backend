"""Support answer writer agent (the ``answer`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import ANSWER_WRITER_PROMPT, build_prompt
from draftly.agents.schemas import AnswerDraft


def build_answer_writer(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the support answer writer agent."""

    return Agent(
        name="support_writer",
        system_prompt=build_prompt(
            ANSWER_WRITER_PROMPT,
            output_model=AnswerDraft,
            support_policy="support_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=AnswerDraft,
        description="Writes answers to support questions.",
    )
