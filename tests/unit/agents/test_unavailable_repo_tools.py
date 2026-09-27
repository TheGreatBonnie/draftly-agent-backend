"""The writer prompt must name the repository tools that are NOT registered.

``read_file`` is registered for the research agents and absent for the PR
writer. A model carrying that prior reached for it 683 times in run d76e2490,
producing one assistant message with 679 tool blocks that hit ``max_tokens``
and failed the page. The prompt's closed-list rule ("a call to any other name
does not exist for this run") was too abstract to change that behaviour;
naming the gap is the fix.
"""

from __future__ import annotations

from draftly.orchestration.graphs.tool_scoping import render_unavailable_repo_tools


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_names_repo_tools_absent_from_the_run() -> None:
    """read_file is registered elsewhere in Draftly, so the writer must be told."""
    tools = [_Tool("github_read_file"), _Tool("github_get_tree")]

    rendered = render_unavailable_repo_tools(tools)

    assert "read_file" in rendered
    assert "list_directory" in rendered


def test_omits_repo_tools_that_are_registered() -> None:
    """A registered name must never be listed as unavailable."""
    tools = [
        _Tool("read_file"),
        _Tool("list_directory"),
        _Tool("github_read_file"),
        _Tool("github_get_tree"),
    ]

    rendered = render_unavailable_repo_tools(tools)

    assert "`read_file`" not in rendered
    assert "`list_directory`" not in rendered
    # The two genuinely absent ones are still named.
    assert "`get_files`" in rendered


def test_renders_empty_when_every_repo_tool_is_registered() -> None:
    tools = [
        _Tool("github_get_tree"),
        _Tool("github_read_file"),
        _Tool("list_directory"),
        _Tool("read_file"),
        _Tool("get_files"),
        _Tool("code_search"),
    ]

    assert render_unavailable_repo_tools(tools) == ""


def test_never_leaks_non_repo_tool_names() -> None:
    """Only REPO_TOOL_HINT_NAMES may appear; other absences stay unlisted.

    Naming all 22 unregistered tools invites more misses than it prevents.
    """
    tools = [_Tool("github_read_file")]

    rendered = render_unavailable_repo_tools(tools)

    assert "create_pull_request" not in rendered
    assert "semantic_search" not in rendered
    assert "write_file" not in rendered


def test_renders_backticked_names_for_prompt_clarity() -> None:
    tools = [_Tool("github_read_file")]

    assert "`read_file`" in render_unavailable_repo_tools(tools)
