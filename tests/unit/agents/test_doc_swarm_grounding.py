"""Documentation research swarm follows the run's grounding mode.

Default / local grounding keeps the local_repo_researcher entry point (no
GitHub API tools — the offline evaluation harness has no usable API).
github grounding swaps in the github_researcher with the read-only GitHub
tools when a real installation backs the run, so real PRs gather evidence
from the API instead of a nonexistent local checkout.
"""

from __future__ import annotations

from draftly.agents.documentation.research_swarm import build_doc_research_swarm
from draftly.app.composition.tools import build_tools
from draftly.orchestration.graphs.tool_scoping import scope_read_only_tools
from tests.stub_model import StubModel

_GITHUB_API_NAMES = {"get_pull_request", "get_files", "get_diff", "get_issue"}


def _agent_tool_names(agent: object) -> set[str]:
    names = getattr(agent, "tool_names", None)
    if isinstance(names, list):
        return set(names)
    raise AttributeError(f"Agent has no list-valued tool_names: {type(agent)}")


def _build_github_tools():
    tools = build_tools()
    return tools, scope_read_only_tools(tools.github_intelligence)


def test_default_swarm_is_local_first() -> None:
    tools = build_tools()
    swarm = build_doc_research_swarm(
        StubModel(),
        tools,
        local_tools=tools.documentation_engineer,
    )

    assert "local_repo_researcher" in set(swarm.nodes)
    assert "github_researcher" not in set(swarm.nodes)
    assert swarm.entry_point.name == "local_repo_researcher"


def test_github_grounding_swaps_in_github_researcher() -> None:
    tools, github_tools = _build_github_tools()
    swarm = build_doc_research_swarm(
        StubModel(),
        tools,
        local_tools=None,
        github_tools=github_tools,
        grounding="github",
    )

    names = set(swarm.nodes)
    assert "github_researcher" in names
    assert "local_repo_researcher" not in names
    assert swarm.entry_point.name == "github_researcher"

    gh_tools = _agent_tool_names(swarm.entry_point)
    assert {"get_pull_request", "get_diff", "get_files"} <= gh_tools


def test_github_researcher_has_no_mutating_github_tools() -> None:
    tools, github_tools = _build_github_tools()
    swarm = build_doc_research_swarm(
        StubModel(),
        tools,
        local_tools=None,
        github_tools=github_tools,
        grounding="github",
    )

    gh_tools = _agent_tool_names(swarm.entry_point)
    assert "create_comment" not in gh_tools


def test_docs_grounding_skips_both_repo_researchers() -> None:
    tools = build_tools()
    swarm = build_doc_research_swarm(
        StubModel(),
        tools,
        local_tools=None,
        grounding="docs",
    )

    names = set(swarm.nodes)
    assert "local_repo_researcher" not in names
    assert "github_researcher" not in names
    assert swarm.entry_point.name == "docs_researcher"
