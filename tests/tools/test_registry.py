"""Unit tests for the scoped tool registry and tool schema rendering."""

from __future__ import annotations

from draftly.app.composition.tools import build_tools, filter_grounded_tools
from draftly.orchestration.graphs.tool_scoping import scope_writer_tools


def test_registry_populates_scoped_groups() -> None:
    registry = build_tools()
    assert registry.github_intelligence
    assert registry.documentation
    assert registry.documentation_engineer
    assert registry.support_engineer
    assert registry.research
    assert registry.github_delivery
    assert registry.all_tools


def test_all_tools_is_deduplicated_union() -> None:
    registry = build_tools()
    identities = [id(tool) for tool in registry.all_tools]
    assert len(identities) == len(set(identities))
    for tool in registry.github_intelligence:
        assert any(id(tool) == i for i in identities)


def test_every_tool_renders_a_schema() -> None:
    registry = build_tools()
    for tool in registry.all_tools:
        spec = tool.tool_spec
        assert spec["name"] == tool.tool_name
        assert spec["description"]
        assert spec["inputSchema"]["json"]["properties"]


def test_group_names_do_not_collide_within_a_group() -> None:
    registry = build_tools()
    for group in (
        registry.documentation,
        registry.documentation_engineer,
        registry.documentation_reviewer,
        registry.github_intelligence,
        registry.support_engineer,
        registry.support_reviewer,
        registry.research,
        registry.github_delivery,
    ):
        names = [tool.tool_name for tool in group]
        assert len(names) == len(set(names)), f"duplicate tool names in group: {names}"


def test_github_intelligence_has_no_local_repo_tools() -> None:
    """github grounding must not let agents walk the local filesystem (/app)."""
    names = [tool.tool_name for tool in build_tools().github_intelligence]
    assert "code_search" not in names
    assert "read_file" not in names
    assert "list_directory" not in names
    assert "git_diff" not in names


def test_github_intelligence_exposes_github_api_repo_tools() -> None:
    names = [tool.tool_name for tool in build_tools().github_intelligence]
    assert "github_search_code" in names
    assert "github_read_file" in names
    assert "github_get_tree" in names


def _all_writer_tools():
    reg = build_tools()
    return scope_writer_tools(reg.documentation_engineer, reg.documentation)


def test_github_grounding_excludes_local_git_fs_tools():
    writer_tools = _all_writer_tools()
    names = {t.tool_name for t in writer_tools}
    github_names = {
        t.tool_name for t in filter_grounded_tools("github", writer_tools)
    }
    for local_name in (
        "read_file",
        "write_file",
        "list_directory",
        "file_exists",
        "git_status",
        "git_diff",
        "git_log",
    ):
        if local_name in names:
            assert local_name not in github_names


def test_local_grounding_keeps_local_fs_tools():
    writer_tools = _all_writer_tools()
    local_names = {
        t.tool_name for t in filter_grounded_tools("local", writer_tools)
    }
    assert local_names == {t.tool_name for t in writer_tools}


def test_filter_grounded_tools_defaults_to_current_grounding():
    writer_tools = _all_writer_tools()
    result = filter_grounded_tools(None, writer_tools)
    assert isinstance(result, list)
