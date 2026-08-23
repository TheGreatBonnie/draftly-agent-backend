"""Documentation writer agent (``update`` / ``create`` nodes)."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import WRITER_PROMPT, build_prompt
from draftly.agents.schemas import DocChangePlan


def build_writer_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the documentation writer agent."""

    return Agent(
        name="doc_writer",
        system_prompt=build_prompt(
            WRITER_PROMPT,
            output_model=DocChangePlan,
            documentation_policy="documentation_policy",
            writing_style="writing_style",
            repository_rules="repository_rules",
            security_rules="security_rules",
        ),
        model=model,
        tools=tools,
        structured_output_model=DocChangePlan,
        description="Writes documentation change plans (create/update).",
    )
