"""Documentation changelog agent (``changelog`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.prompts import CHANGELOG_PROMPT, build_prompt
from draftly.agents.schemas import ChangelogEntry


def build_changelog_agent(
    model: Any,
    tools: list[Any],
) -> Agent:
    """Build the changelog generation agent.

    Same tool restrictions as the doc writer: read/analyze only, no mutation.
    """

    return Agent(
        name="changelog_writer",
        system_prompt=build_prompt(
            CHANGELOG_PROMPT,
            output_model=ChangelogEntry,
        ),
        model=model,
        tools=tools,
        structured_output_model=ChangelogEntry,
        description="Generates Keep a Changelog entries for release events.",
    )
