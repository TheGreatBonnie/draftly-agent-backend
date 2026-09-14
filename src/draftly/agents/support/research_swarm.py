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

from strands.multiagent import Swarm
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import SUPPORT_LOCAL_RESEARCHER_PROMPT, load_skills
from draftly.agents.shared.research import (
    build_discord_researcher,
    build_docs_researcher,
    build_slack_researcher,
)
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_support_research_swarm(
    model: Any,
    tools: Any,
    *,
    local_tools: list[Any],
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
    execution_timeout: float | None = None,
    node_timeout: float | None = None,
) -> Swarm:
    """Build the support research swarm whose ``research`` node grounds locally.

    Args:
        local_tools: repo-scoped tools (semantic_search, keyword_search,
            code_search, ...) the local researcher inspects the checkout with.
        execution_timeout: swarm-wide budget; defaults to 1800s.
        node_timeout: per-agent budget; defaults to 600s unless overridden by
            the operator-configured Strands budgets.
    """
    local_agent = build_draftly_agent(
        role=AgentRole.RESEARCH,
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
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "local_repo_researcher",
        node_id=node_id or "support_research",
        name="local_repo_researcher",
        description="Researches local repository evidence for the support question.",
    )
    slack_agent = build_slack_researcher(
        model,
        [tools.slack_search, tools.slack_get_thread],
        runtime=runtime,
        node_id=node_id or "support_research",
    )
    discord_agent = build_discord_researcher(
        model,
        [tools.discord_search, tools.discord_get_thread],
        runtime=runtime,
        node_id=node_id or "support_research",
    )
    docs_agent = build_docs_researcher(
        model,
        [tools.semantic_search, tools.keyword_search, tools.hybrid_search],
        runtime=runtime,
        node_id=node_id or "support_research",
    )

    return Swarm(
        [local_agent, slack_agent, discord_agent, docs_agent],
        entry_point=local_agent,
        max_handoffs=20,
        max_iterations=20,
        execution_timeout=execution_timeout if execution_timeout is not None else 1800.0,
        node_timeout=node_timeout if node_timeout is not None else 600.0,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
