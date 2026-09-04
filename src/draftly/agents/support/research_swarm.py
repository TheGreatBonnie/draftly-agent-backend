"""GitHub support research swarm: local-repo-first, with channel researchers.

Mirrors the issue research swarm for the ``research`` node of the support
surface. Online support evaluation is backed by a local authly checkout with
no usable remote tools, so the local-repo researcher leads and is required to
perform an actual retrieval/code grounding search before returning (satisfying
the support dataset's ``required_tools`` for the research node). Slack/Discord
researchers are still present for production runs that carry those sources.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.multiagent import Swarm
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.prompts import SUPPORT_LOCAL_RESEARCHER_PROMPT, load_skills
from draftly.agents.shared.research import (
    build_discord_researcher,
    build_docs_researcher,
    build_slack_researcher,
)


def build_support_research_swarm(
    model: Any,
    tools: Any,
    *,
    local_tools: list[Any],
) -> Swarm:
    """Build the support research swarm whose ``research`` node grounds locally.

    Args:
        local_tools: repo-scoped tools (semantic_search, keyword_search,
            code_search, ...) the local researcher inspects the checkout with.
    """
    local_agent = Agent(
        name="local_repo_researcher",
        system_prompt=SUPPORT_LOCAL_RESEARCHER_PROMPT,
        model=model,
        tools=local_tools,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "repository-analysis",
                    "support-answering",
                )
            )
        ],
        description="Researches local repository evidence for the support question.",
    )
    slack_agent = build_slack_researcher(
        model,
        [tools.slack_search, tools.slack_get_thread],
    )
    discord_agent = build_discord_researcher(
        model,
        [tools.discord_search, tools.discord_get_thread],
    )
    docs_agent = build_docs_researcher(
        model,
        [tools.semantic_search, tools.keyword_search, tools.hybrid_search],
    )

    return Swarm(
        [local_agent, slack_agent, discord_agent, docs_agent],
        entry_point=local_agent,
        max_handoffs=20,
        max_iterations=20,
        execution_timeout=900.0,
        node_timeout=300.0,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
