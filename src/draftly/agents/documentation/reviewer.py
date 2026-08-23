"""Documentation reviewer agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import REVIEWER_PROMPT, build_prompt
from draftly.agents.schemas import EvaluationResult


def build_reviewer_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation reviewer agent."""

    return Agent(
        name="doc_reviewer",
        system_prompt=build_prompt(
            REVIEWER_PROMPT,
            output_model=EvaluationResult,
            evaluation_rules="evaluation_rules",
            documentation_policy="documentation_policy",
        ),
        model=model,
        tools=tools,
        structured_output_model=EvaluationResult,
        description="Reviews documentation changes against policy.",
    )
