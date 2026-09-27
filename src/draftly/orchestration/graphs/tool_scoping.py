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


# Read-only repository-navigation tools that prompts may name when instructing
# an agent where to enumerate/inspect the repository tree. Each run registers a
# different subset (local checkout vs GitHub-API vs docs-only), so prompts
# derive the hint from the tools actually passed in and never name a phantom.
REPO_TOOL_HINT_NAMES = (
    "github_get_tree",
    "github_read_file",
    "list_directory",
    "read_file",
    "get_files",
    "code_search",
)

FALLBACK_REPO_HINT = "your read-only repository tools"


def repo_tool_hint(tools: list[Any]) -> str:
    """Name the repository tools registered for this run, or a safe fallback."""
    registered = {tool_name(tool) for tool in tools}
    present = [name for name in REPO_TOOL_HINT_NAMES if name in registered]
    return ", ".join(present) if present else FALLBACK_REPO_HINT


def render_unavailable_repo_tools(tools: list[Any]) -> str:
    """Name the repository-tool aliases that are NOT registered for this run.

    ``read_file`` is registered for the research agents and absent for the PR
    writer. A model carrying that prior reached for it 683 times in run
    d76e2490, emitting 679 tool blocks in one message that hit ``max_tokens``
    and failed the page. The prompt's closed-list rule was too abstract to
    change that; naming the gap by name is the fix.

    Only ``REPO_TOOL_HINT_NAMES`` is considered. Naming every unregistered
    tool (22 of them for a writer run) invites more misses than it prevents.
    Returns ``""`` when all of them are registered, so the caller can drop
    the sentence entirely.
    """
    registered = {name for tool in tools if (name := tool_name(tool))}
    absent = [name for name in REPO_TOOL_HINT_NAMES if name not in registered]
    return ", ".join(f"`{name}`" for name in absent)


def render_tool_names(tools: list[Any]) -> str:
    """Render the run's registered tool names as an inline backticked list.

    The writer prompt states the CLOSED list of callable names. Run e1e96f90
    spent 19 turns on calls to names that were never registered (``read_file``,
    ``bash``, ``github_get_file``, ``get_pull_request_diff``) because the prompt
    described the toolset only in the abstract; a model with a coding-agent prior
    fills that gap with plausible names, and every miss costs a whole turn.
    """
    names = sorted({name for tool in tools if (name := tool_name(tool))})
    if not names:
        return "(no tools are registered for this run)"
    return ", ".join(f"`{name}`" for name in names)
