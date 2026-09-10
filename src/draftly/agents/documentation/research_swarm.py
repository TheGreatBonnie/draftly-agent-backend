"""Documentation research swarm: grounding-aware evidence collection.

The swarm follows the run's grounding: ``local`` spawns a local-repo
researcher (default — the offline evaluation harness has a local authly
worktree and no usable GitHub API), ``github`` spawns a GitHub researcher
scoped to the read-only GitHub API tools for real linked PRs, and ``docs``
uses only the documentation/slack/discord researchers.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.multiagent import Swarm
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import load_skills, local_repo_note_for
from draftly.agents.shared.research import (
    build_discord_researcher,
    build_docs_researcher,
    build_github_researcher,
    build_slack_researcher,
)
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole
from draftly.workflows.grounding import DOCS, GITHUB


def _local_researcher_prompt(repo_dir: str | None) -> str:
    """System prompt for the local-repo researcher.

    Anchors the researcher to the concrete ``repo_dir`` when one is known so
    repo tools never silently operate on the wrong checkout (the container
    cwd). Keeps the GitHub-API prohibition hard-coded — offline harness runs
    have no usable API regardless of the checkout.
    """
    note = local_repo_note_for(repo_dir)
    if note:
        return (
            "You research the LOCAL repository checkout for evidence relevant "
            "to the event. The PR/diff/changed files are already provided in "
            "the task context; confirm them with the local repository tools "
            "(code_search, read_file, list_directory, git_*) by ALWAYS passing "
            f"repo_dir=<{repo_dir}>. Do NOT call GitHub web tools "
            "(get_pull_request, get_files, get_diff); never operate on files "
            "outside that checkout root. Collect concrete source ids (file "
            "paths + line numbers)."
        )
    return (
        "You research the LOCAL repository checkout for evidence relevant to "
        "the event. The PR/diff/changed files are already provided in the task "
        "context; confirm them with the local repository tools (code_search, "
        "read_file, list_directory, git_*). When a checkout path is known it "
        "is passed as repo_dir=<local checkout path> to the repo tools. Do NOT "
        "call GitHub web tools (get_pull_request, get_files, get_diff): the "
        "network API is unavailable. Collect concrete source ids (file paths "
        "+ line numbers)."
    )


def _local_researcher(
    model: Any,
    local_tools: list[Any],
    repo_dir: str | None,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=_local_researcher_prompt(repo_dir),
        model=model,
        tools=local_tools,
        plugins=[AgentSkills(skills=load_skills("github-pr-analysis", "github-release-analysis"))],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "local_repo_researcher",
        node_id=node_id or "doc_research",
        name="local_repo_researcher",
        description="Researches local repository evidence for the event.",
    )


def build_doc_research_swarm(
    model: Any,
    tools: Any,
    local_tools: list[Any] | None = None,
    *,
    repo_dir: str | None = None,
    github_tools: list[Any] | None = None,
    grounding: str = "local",
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Swarm:
    """Build the research swarm used by the documentation graph."""

    slack_agent = build_slack_researcher(
        model,
        [tools.slack_search, tools.slack_get_thread],
        runtime=runtime,
        node_id=node_id or "doc_research",
    )
    discord_agent = build_discord_researcher(
        model,
        [tools.discord_search, tools.discord_get_thread],
        runtime=runtime,
        node_id=node_id or "doc_research",
    )
    docs_agent = build_docs_researcher(
        model,
        [tools.semantic_search, tools.keyword_search, tools.hybrid_search],
        runtime=runtime,
        node_id=node_id or "doc_research",
    )

    if grounding == GITHUB:
        github_agent = build_github_researcher(
            model,
            github_tools or [],
            runtime=runtime,
            node_id=node_id or "doc_research",
        )
        agents = [github_agent, slack_agent, discord_agent, docs_agent]
        entry_point = github_agent
    elif grounding == DOCS:
        agents = [docs_agent, slack_agent, discord_agent]
        entry_point = docs_agent
    else:
        local_agent = _local_researcher(
            model, local_tools or [], repo_dir, runtime=runtime, node_id=node_id or "doc_research"
        )
        agents = [local_agent, slack_agent, discord_agent, docs_agent]
        entry_point = local_agent

    return Swarm(
        agents,
        entry_point=entry_point,
        max_handoffs=20,
        max_iterations=20,
        execution_timeout=900.0,
        node_timeout=300.0,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
