"""GitHub issue researcher agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import RESEARCH_PROMPT, build_prompt


def build_issue_researcher(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the GitHub issue researcher agent."""

    return Agent(
        name="issue_researcher",
        system_prompt=build_prompt(
            RESEARCH_PROMPT,
            support_policy="support_policy",
        ),
        model=model,
        tools=tools,
        description="Researches GitHub issues for context and solutions.",
    )
