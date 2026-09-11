"""Research agent definitions (shared across surfaces)."""

from __future__ import annotations

from typing import Any

from strands import Agent

from draftly.agents.factory import build_draftly_agent
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_github_researcher(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """GitHub-focused researcher sub-agent."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=(
            "You research GitHub issues, pull requests, diffs, and code "
            "for evidence relevant to the event. Collect concrete source ids."
        ),
        model=model,
        tools=tools,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "github_researcher",
        node_id=node_id or "github_researcher",
        name="github_researcher",
        description="Researches GitHub evidence for the event.",
    )


def build_slack_researcher(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Slack-history researcher sub-agent."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=(
            "You search Slack history for related conversations and answers "
            "relevant to the event. Collect concrete message references."
        ),
        model=model,
        tools=tools,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "slack_researcher",
        node_id=node_id or "slack_researcher",
        name="slack_researcher",
        description="Researches Slack history for the event.",
    )


def build_discord_researcher(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Discord-history researcher sub-agent."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=(
            "You search Discord history for related conversations and answers "
            "relevant to the event. Collect concrete message references."
        ),
        model=model,
        tools=tools,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "discord_researcher",
        node_id=node_id or "discord_researcher",
        name="discord_researcher",
        description="Researches Discord history for the event.",
    )


def build_docs_researcher(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    """Documentation-store researcher sub-agent."""

    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=(
            "You search the documentation store (semantic, keyword, hybrid) "
            "and report coverage and gaps for the event topic."
        ),
        model=model,
        tools=tools,
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "docs_researcher",
        node_id=node_id or "docs_researcher",
        name="docs_researcher",
        description="Researches documentation coverage for the event.",
    )
