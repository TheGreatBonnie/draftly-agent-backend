"""Research swarm: four channel-scoped researchers handing off autonomously."""

from __future__ import annotations

from typing import Any

from strands.multiagent import Swarm

from draftly.agents.shared.research import (
    build_discord_researcher,
    build_docs_researcher,
    build_github_researcher,
    build_slack_researcher,
)
from draftly.steering.context import SteeringRuntime


def build_research_swarm(
    model: Any,
    tools: Any,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Swarm:
    """Build the 4-agent research swarm used by the ``research`` node."""

    github_agent = build_github_researcher(
        model,
        tools.github_intelligence,
        runtime=runtime,
        node_id=node_id or "research",
    )
    slack_agent = build_slack_researcher(
        model,
        [tools.slack_search, tools.slack_get_thread],
        runtime=runtime,
        node_id=node_id or "research",
    )
    discord_agent = build_discord_researcher(
        model,
        [tools.discord_search, tools.discord_get_thread],
        runtime=runtime,
        node_id=node_id or "research",
    )
    docs_agent = build_docs_researcher(
        model,
        [tools.semantic_search, tools.keyword_search, tools.hybrid_search],
        runtime=runtime,
        node_id=node_id or "research",
    )

    return Swarm(
        [github_agent, slack_agent, discord_agent, docs_agent],
        entry_point=github_agent,
        max_handoffs=20,
        max_iterations=20,
        execution_timeout=900.0,
        node_timeout=300.0,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
