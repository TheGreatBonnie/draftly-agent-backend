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

from strands import Agent
from strands.multiagent import Swarm
from strands.vended_plugins.skills import AgentSkills

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


def build_issue_research_swarm(
    model: Any,
    tools: Any,
    *,
    local_tools: list[Any],
    github_tools: list[Any] | None = None,
) -> Swarm:
    """Build the research swarm used by the GitHub issue ``research`` node.

    Args:
        local_tools: local-repo tools the researcher inspects the checkout with
            (code_search, semantic_search, keyword_search, ...).
        github_tools: optional GitHub network tools; when non-empty a github
            researcher is appended after the local researcher (production runs
            with a real token), otherwise it is omitted (online evaluation).
    """
    local_agent = Agent(
        name="local_repo_researcher",
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
        description="Researches local repository evidence for the issue.",
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

    agents = [local_agent, slack_agent, discord_agent, docs_agent]
    if github_tools:
        github_agent = build_github_researcher(model, github_tools)
        agents.append(github_agent)

    return Swarm(
        agents,
        entry_point=local_agent,
        max_handoffs=20,
        max_iterations=20,
        execution_timeout=900.0,
        node_timeout=300.0,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
