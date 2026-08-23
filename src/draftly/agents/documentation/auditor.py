"""Documentation auditor agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import REVIEWER_PROMPT, build_prompt
from draftly.agents.schemas import EvaluationResult


def build_auditor_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation auditor agent (stale/coverage checks)."""

    return Agent(
        name="doc_auditor",
        system_prompt=(
            "You audit the documentation store for staleness, broken links, "
            "and coverage gaps. "
            + build_prompt(
                REVIEWER_PROMPT,
                output_model=EvaluationResult,
                evaluation_rules="evaluation_rules",
                documentation_policy="documentation_policy",
            )
        ),
        model=model,
        tools=tools,
        description="Audits documentation for staleness and gaps.",
    )
