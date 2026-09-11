"""Documentation changelog agent (``changelog`` node)."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import CHANGELOG_PROMPT, build_prompt
from draftly.agents.schemas import ChangelogEntry
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_changelog_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Build the changelog generation agent.

    Same tool restrictions as the doc writer: read/analyze only, no mutation.
    """

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            CHANGELOG_PROMPT,
            output_model=ChangelogEntry,
        ),
        model=model,
        tools=tools,
        structured_output_model=ChangelogEntry,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "changelog_writer",
        node_id=node_id or "changelog_writer",
        name="changelog_writer",
        description="Generates Keep a Changelog entries for release events.",
    )
