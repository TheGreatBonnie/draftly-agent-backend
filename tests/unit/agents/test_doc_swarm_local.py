"""Documentation graph research swarm is local-first (no GitHub API tools).

The online evaluation harness backs each case with a local authly worktree and
has no usable GitHub API. If the docs research/context agents are handed the
remote GitHub tools (get_pull_request / get_files / get_diff) they 401-loop and
burn the node budget. These tests lock in the local-first wiring.
"""

from __future__ import annotations

from draftly.agents.documentation.context import build_doc_context_agent
from draftly.agents.documentation.research_swarm import build_doc_research_swarm
from draftly.app.composition.tools import build_tools
from tests.stub_model import StubModel

_GITHUB_API_NAMES = {"get_pull_request", "get_files", "get_diff", "get_issue"}


def _tool_names(agent: object) -> set[str]:
    return {
        t.tool_name if hasattr(t, "tool_name") else str(t)
        for t in agent.tools
    }


def _build_swarm():
    tools = build_tools()
    return build_doc_research_swarm(
        StubModel(),
        tools,
        local_tools=tools.documentation_engineer,
    )


def test_doc_research_swarm_has_local_agent_and_no_github_api() -> None:
    swarm = _build_swarm()
    names = {w.name for w in swarm.workers}

    assert "local_repo_researcher" in names
    assert "github_researcher" not in names  # the 401-looping agent is gone

    local = next(w for w in swarm.workers if w.name == "local_repo_researcher")
    local_tools = _tool_names(local)

    assert _GITHUB_API_NAMES.isdisjoint(local_tools)
    # local browsing + git + search tools are present
    assert {"read_file", "list_directory", "file_exists", "git_diff"} <= local_tools


def test_doc_context_agent_excludes_github_api_tools() -> None:
    tools = build_tools()
    agent = build_doc_context_agent(StubModel(), tools.documentation_engineer)
    context_tools = _tool_names(agent)

    assert _GITHUB_API_NAMES.isdisjoint(context_tools)
    assert {"read_file", "list_directory", "git_diff", "git_status"} <= context_tools
