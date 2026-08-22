"""Documentation auditor agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import REVIEWER_PROMPT


def build_auditor_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation auditor agent (stale/coverage checks)."""

    return Agent(
        name="doc_auditor",
        system_prompt=(
            "You audit the documentation store for staleness, broken links, "
            "and coverage gaps. " + REVIEWER_PROMPT
        ),
        model=model,
        tools=tools,
        description="Audits documentation for staleness and gaps.",
    )
