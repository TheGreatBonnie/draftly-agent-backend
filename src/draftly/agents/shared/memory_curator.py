"""Memory curator agent: curates candidates into durable long-term knowledge."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import MEMORY_CURATOR_PROMPT


def build_memory_curator(
    model: Any,
    tools: list[Any] | None = None,
) -> Agent:
    """Build the memory curation agent with its read/write toolset."""

    return Agent(
        name="memory_curator",
        system_prompt=MEMORY_CURATOR_PROMPT,
        model=model,
        tools=list(tools or []),
        description="Curates long-term memory candidates into durable knowledge.",
    )
