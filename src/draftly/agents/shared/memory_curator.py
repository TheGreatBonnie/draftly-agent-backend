"""Memory curator agent: consolidates and ranks memory items."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import MEMORY_CURATOR_PROMPT


def build_memory_curator(
    model: Any,
    tools: list[Any] | None = None,
) -> Agent:
    """Build the memory curation agent."""

    return Agent(
        name="memory_curator",
        system_prompt=MEMORY_CURATOR_PROMPT,
        model=model,
        tools=tools or [],
        description="Consolidates and ranks memory items.",
    )
