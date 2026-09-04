"""Documentation research swarm: local-repo-first, drops the GitHub API agent.

The online evaluation harness backs every case with a local authly worktree
and has no usable GitHub API, so the docs graph must not spawn a github
researcher that 401-loops. Instead it spawns a local-repo researcher scoped to
the checkout tools plus the documentation/slack/discord researchers.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.multiagent import Swarm

from draftly.agents.shared.research import (
    build_discord_researcher,
    build_docs_researcher,
    build_slack_researcher,
)


def build_doc_research_swarm(model: Any, tools: Any, local_tools: list[Any]) -> Swarm:
    """Build the research swarm used by the documentation graph."""

    local_agent = Agent(
        name="local_repo_researcher",
        system_prompt=(
            "You research the LOCAL repository checkout for evidence relevant "
            "to the event. The PR/diff/changed files are already provided in "
            "the task context; confirm them with the local repository tools "
            "(code_search, read_file, list_directory, git_*) using repo_dir=<local "
            "checkout path>. Do NOT call GitHub web tools (get_pull_request, "
            "get_files, get_diff): the network API is unavailable. Collect "
            "concrete source ids (file paths + line numbers)."
        ),
        model=model,
        tools=local_tools,
        description="Researches local repository evidence for the event.",
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
