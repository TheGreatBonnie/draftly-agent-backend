"""Legacy GitHub read aliases execute the canonical implementation."""

from __future__ import annotations

import pytest

from draftly.tools.github.compat import (
    LEGACY_CANONICAL_TOOL_NAMES,
    github_get_file,
    github_list_tree,
)


class _FakeClient:
    """Stands in for GitHubClient; records requests and returns canned data."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def get_file_contents(self, owner: str, repo: str, path: str, ref: str) -> str:
        self.calls.append(("get_file_contents", owner, repo, path, ref))
        return f"{owner}/{repo}:{path}@{ref}"

    async def get_tree_bounded(
        self, owner, repo, ref, path_prefix=None, max_entries=None
    ) -> dict:
        self.calls.append(("get_tree_bounded", owner, repo, ref, path_prefix, max_entries))
        return {"entries": [{"path": "docs/a.md"}], "returned": 1, "truncated": False}


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    """Routes the client imports inside the tool bodies to the fake."""
    monkeypatch.setattr("draftly.integrations.github.client.GitHubClient", lambda: fake)


def _silence_scope_guards(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Record reserve_writer_read calls, and no-op the target guard."""
    seen: list[tuple] = []
    monkeypatch.setattr(
        "draftly.tools.github.writer_scope.reserve_writer_read",
        lambda *args: seen.append(args),
    )
    monkeypatch.setattr(
        "draftly.tools.github.writer_scope.require_writer_target",
        lambda *args, **kwargs: None,
    )
    return seen


def test_alias_tools_are_registered_under_the_legacy_names() -> None:
    # `.tool_name` drives the closed-list prompt (render_tool_names) and the
    # ToolRegistryGuard availability check, so registration is the whole point.
    assert github_get_file.tool_name == "github_get_file"
    assert github_list_tree.tool_name == "github_list_tree"


def test_legacy_name_map_points_at_the_canonical_tools() -> None:
    assert LEGACY_CANONICAL_TOOL_NAMES == {
        "github_get_file": "github_read_file",
        "github_list_tree": "github_get_tree",
    }


async def test_get_file_alias_reserves_the_canonical_read_budget_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The alias charges the SAME budget key as github_read_file.

    Deliberately exercised via ``_tool_func`` (the original wrapped function
    DecoratedFunctionTool stores): it is the stable way to run an isolated
    @tool without a full Strands event loop.
    """
    seen = _silence_scope_guards(monkeypatch)
    _patch_client(monkeypatch, _FakeClient())

    body = await github_get_file._tool_func("TheGreatBonnie", "authly", "oauth.py", "abc123")

    assert seen == [
        ("github_read_file", "TheGreatBonnie", "authly", "oauth.py", "abc123")
    ]
    assert body == "TheGreatBonnie/authly:oauth.py@abc123"


async def test_list_tree_alias_reserves_the_canonical_read_budget_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _silence_scope_guards(monkeypatch)
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = await github_list_tree._tool_func("o", "r", "abc", path_prefix="docs")

    assert seen == [("github_get_tree", "o", "r", "abc", "docs")]
    assert result["entries"][0]["path"] == "docs/a.md"
    assert fake.calls[0][0] == "get_tree_bounded"
