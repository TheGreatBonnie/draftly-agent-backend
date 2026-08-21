"""Documentation researcher agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import RESEARCH_PROMPT, build_prompt


def build_documentation_researcher(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation researcher agent."""

    return Agent(
        name="doc_researcher",
        system_prompt=build_prompt(
            RESEARCH_PROMPT,
            support_policy="support_policy",
        ),
        model=model,
        tools=tools,
        description="Researches documentation coverage and gaps.",
    )
