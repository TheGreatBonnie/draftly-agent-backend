"""Documentation researcher agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import RESEARCH_PROMPT, build_prompt
from draftly.agents.schemas import EvidenceBundle


def build_documentation_researcher(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation researcher agent."""

    return Agent(
        name="doc_researcher",
        system_prompt=build_prompt(
            RESEARCH_PROMPT,
            output_model=EvidenceBundle,
            support_policy="support_policy",
            documentation_policy="documentation_policy",
        ),
        model=model,
        tools=tools,
        description="Researches documentation coverage and gaps.",
    )
