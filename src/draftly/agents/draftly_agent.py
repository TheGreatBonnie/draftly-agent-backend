"""Root Draftly agent."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import CLASSIFIER_PROMPT


def create_draftly_agent(
    model: Any,
    tools: list[Any] | None = None,
) -> Agent:
    """Build the root Draftly agent."""

    return Agent(
        name="draftly",
        system_prompt=(
            "You are Draftly, an autonomous documentation-engineering agent. "
            + CLASSIFIER_PROMPT
        ),
        model=model,
        tools=tools or [],
        description="Root Draftly documentation-intelligence agent.",
    )
