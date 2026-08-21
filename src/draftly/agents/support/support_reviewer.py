"""Support reviewer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import REVIEWER_PROMPT
from draftly.agents.schemas import EvaluationResult


def build_support_reviewer(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the support answer reviewer agent."""

    return Agent(
        name="support_reviewer",
        system_prompt=REVIEWER_PROMPT,
        model=model,
        tools=tools,
        structured_output_model=EvaluationResult,
        description="Reviews support answers for accuracy and completeness.",
    )
