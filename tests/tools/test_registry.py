"""Unit tests for the scoped tool registry and tool schema rendering."""

from __future__ import annotations

from draftly.app.composition.tools import build_tools


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
