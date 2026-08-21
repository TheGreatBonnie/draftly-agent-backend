"""Research agent definitions (shared across surfaces)."""

from __future__ import annotations

from typing import Any

from strands import Agent


def build_github_researcher(model: Any, tools: list[Any]) -> Agent:
    """GitHub-focused researcher sub-agent."""

    return Agent(
        name="github_researcher",
        system_prompt=(
            "You research GitHub issues, pull requests, diffs, and code "
            "for evidence relevant to the event. Collect concrete source ids."
        ),
        model=model,
        tools=tools,
        description="Researches GitHub evidence for the event.",
    )


def build_slack_researcher(model: Any, tools: list[Any]) -> Agent:
    """Slack-history researcher sub-agent."""

    return Agent(
        name="slack_researcher",
        system_prompt=(
            "You search Slack history for related conversations and answers "
            "relevant to the event. Collect concrete message references."
        ),
        model=model,
        tools=tools,
        description="Researches Slack history for the event.",
    )


def build_discord_researcher(model: Any, tools: list[Any]) -> Agent:
    """Discord-history researcher sub-agent."""

    return Agent(
        name="discord_researcher",
        system_prompt=(
            "You search Discord history for related conversations and answers "
            "relevant to the event. Collect concrete message references."
        ),
        model=model,
        tools=tools,
        description="Researches Discord history for the event.",
    )


def build_docs_researcher(model: Any, tools: list[Any]) -> Agent:
    """Documentation-store researcher sub-agent."""

    return Agent(
        name="docs_researcher",
        system_prompt=(
            "You search the documentation store (semantic, keyword, hybrid) "
            "and report coverage and gaps for the event topic."
        ),
        model=model,
        tools=tools,
        description="Researches documentation coverage for the event.",
    )
