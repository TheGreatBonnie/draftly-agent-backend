"""Support solution researcher agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import RESEARCH_PROMPT, build_prompt


def build_solution_researcher(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the support solution researcher agent."""

    return Agent(
        name="support_researcher",
        system_prompt=build_prompt(
            RESEARCH_PROMPT,
            support_policy="support_policy",
        ),
        model=model,
        tools=tools,
        description="Researches solutions for support questions.",
    )
