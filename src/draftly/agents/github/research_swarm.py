"""GitHub issue research swarm: local-repo first, with optional github agent.

The docs graph drops the GitHub API researcher because online evaluation backs
every case with a local worktree and no usable token. The issue surface has
the same constraint when evaluated locally (``repo_dir`` set, no token), so
the issue research swarm leads with a local-repo researcher scoped to the
checkout code/docs tools and follows with the slack/discord/docs researchers.

A ``github_agent`` is only included when ``include_github`` is True (i.e. a
real ``GITHUB_TOKEN`` is available at runtime); online evaluation omits it so
it cannot 401-loop on ``get_issue``.
"""

from __future__ import annotations

from typing import Any

from strands.multiagent import Swarm
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import (
    ISSUE_LOCAL_RESEARCHER_PROMPT,
    load_skills,
)
from draftly.agents.shared.research import (
    build_discord_researcher,
    build_docs_researcher,
    build_github_researcher,
    build_slack_researcher,
)
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole


def build_issue_research_swarm(
    model: Any,
    tools: Any,
    *,
    local_tools: list[Any],
    github_tools: list[Any] | None = None,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
    execution_timeout: float | None = None,
    node_timeout: float | None = None,
) -> Swarm:
    """Build the research swarm used by the GitHub issue ``research`` node.

    Args:
        local_tools: local-repo tools the researcher inspects the checkout with
            (code_search, semantic_search, keyword_search, ...).
        github_tools: optional GitHub network tools; when non-empty a github
            researcher is appended after the local researcher (production runs
            with a real token), otherwise it is omitted (online evaluation).
        execution_timeout: swarm-wide budget; defaults to 1800s.
        node_timeout: per-agent budget; defaults to 600s unless overridden by
            the operator-configured Strands budgets.
    """
    local_agent = build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=ISSUE_LOCAL_RESEARCHER_PROMPT,
        model=model,
        tools=local_tools,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "github-issue-analysis",
                    "repository-analysis",
                )
            )
        ],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "local_repo_researcher",
        node_id=node_id or "issue_research",
        name="local_repo_researcher",
        description="Researches local repository evidence for the issue.",
    )
    slack_agent = build_slack_researcher(
        model,
        [tools.slack_search, tools.slack_get_thread],
        runtime=runtime,
        node_id=node_id or "issue_research",
    )
    discord_agent = build_discord_researcher(
        model,
        [tools.discord_search, tools.discord_get_thread],
        runtime=runtime,
        node_id=node_id or "issue_research",
    )
    docs_agent = build_docs_researcher(
        model,
        [tools.semantic_search, tools.keyword_search, tools.hybrid_search],
        runtime=runtime,
        node_id=node_id or "issue_research",
    )

    agents = [local_agent, slack_agent, discord_agent, docs_agent]
    if github_tools:
        github_agent = build_github_researcher(
            model,
            github_tools,
            runtime=runtime,
            node_id=node_id or "issue_research",
        )
        agents.append(github_agent)

    return Swarm(
        agents,
        entry_point=local_agent,
        max_handoffs=20,
        max_iterations=20,
        execution_timeout=execution_timeout if execution_timeout is not None else 1800.0,
        node_timeout=node_timeout if node_timeout is not None else 600.0,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
