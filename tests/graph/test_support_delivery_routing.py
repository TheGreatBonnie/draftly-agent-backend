"""Support delivery routing: origin-platform posting vs GitHub handoff."""

from __future__ import annotations

from draftly.app.composition.tools import build_tools
from draftly.integrations.strands.graph import build_graph_for_run
from draftly.orchestration.graphs.support_graph import _delivery_tools_for_source
from draftly.workflows.support.models import DocumentationGapRequest
from draftly.workflows.support.support_resolution import route_support_outcome


def tool_names(tools):
    return {
        getattr(tool, "name", None)
        or getattr(getattr(tool, "fn", None), "__name__", None)
        or getattr(tool, "tool_name", None)
        or getattr(tool, "__name__", None)
        for tool in tools
    }


def test_direct_support_answer_exposes_only_origin_platform_delivery() -> None:
    tools = build_tools()
    assert tool_names(_delivery_tools_for_source("slack", tools)) == {
        "slack_post_message"
    }
    assert tool_names(_delivery_tools_for_source("discord", tools)) == {
        "discord_post_message"
    }


def test_github_delivery_exposes_only_documentation_tools() -> None:
    tools = build_tools()
    assert tool_names(_delivery_tools_for_source("github", tools)) == {
        "create_branch",
        "create_commit",
        "create_pull_request",
        "create_comment",
    }


def _deliver_tool_names(graph) -> set[str]:
    return {str(name) for name in graph.nodes["deliver"].executor.tool_names}


def test_slack_support_graph_scopes_delivery_to_slack_only(
    model, tools, tmp_sessions
) -> None:
    graph = build_graph_for_run(
        "slack-route-1",
        surface="slack",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    names = _deliver_tool_names(graph)
    assert "slack_post_message" in names
    assert "discord_post_message" not in names


def test_discord_support_graph_scopes_delivery_to_discord_only(
    model, tools, tmp_sessions
) -> None:
    graph = build_graph_for_run(
        "discord-route-1",
        surface="discord",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
    )
    names = _deliver_tool_names(graph)
    assert "discord_post_message" in names
    assert "slack_post_message" not in names


def test_documentation_gap_routes_to_github_delivery() -> None:
    request = DocumentationGapRequest(
        org_id="org-1",
        repository="acme/docs",
        base_branch="main",
        source_event_ids=["evt-1"],
    )
    assert route_support_outcome(request) == "github"
