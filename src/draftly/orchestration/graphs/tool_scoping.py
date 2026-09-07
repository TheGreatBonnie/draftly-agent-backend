"""Least-privilege tool scopes shared by workflow graphs."""

from __future__ import annotations

from typing import Any

_MUTATING_TOOL_NAMES = frozenset(
    {
        "write_file",
        "update_frontmatter",
        "create_branch",
        "create_commit",
        "create_pull_request",
        "create_comment",
        "slack_post_message",
        "discord_post_message",
    }
)


def tool_name(tool: Any) -> str | None:
    return (
        getattr(tool, "name", None)
        or getattr(getattr(tool, "fn", None), "__name__", None)
        or getattr(tool, "__name__", None)
    )


def dedupe_tools(*groups: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[int] = set()
    for group in groups:
        for tool in group:
            if id(tool) not in seen:
                seen.add(id(tool))
                result.append(tool)
    return result


def scope_read_only_tools(*groups: list[Any]) -> list[Any]:
    """Return tools safe for evidence gathering and analysis agents."""
    return [
        tool
        for tool in dedupe_tools(*groups)
        if tool_name(tool) not in _MUTATING_TOOL_NAMES
    ]


def scope_writer_tools(*groups: list[Any]) -> list[Any]:
    """Return tools safe for producing a change plan, not applying it."""
    return scope_read_only_tools(*groups)
