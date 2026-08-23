"""Context agent: gathers an evidence bundle for the incoming event."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import CONTEXT_PROMPT, build_prompt
from draftly.agents.schemas import EvidenceBundle


def build_context_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the context agent (evidence collection)."""

    return Agent(
        name="context",
        system_prompt=build_prompt(
            CONTEXT_PROMPT,
            output_model=EvidenceBundle,
            documentation_policy="documentation_policy",
            repository_rules="repository_rules",
        ),
        model=model,
        tools=tools,
        structured_output_model=EvidenceBundle,
        description="Collects evidence about the event from GitHub, search, and docs.",
    )
