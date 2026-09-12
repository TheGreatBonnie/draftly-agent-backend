"""GitHub tool round-trips with FakeGitHubClient (plan §11.2).

Tools construct ``GitHubClient()`` inside the function body, so tests
monkeypatch the class at its source module.
"""

from __future__ import annotations

import pytest
from strands.tools import tool as tool_decorator

from tests.fakes import FakeGitHubClient


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHubClient:
    fake = FakeGitHubClient()
    monkeypatch.setattr("draftly.integrations.github.client.GitHubClient", lambda: fake)
    return fake


async def test_get_diff_round_trip(fake_github: FakeGitHubClient) -> None:
    from draftly.tools.github.get_diff import get_diff

    result = await get_diff("acme", "widget", 7)

    assert "diff --git" in result
    assert ("get_pull_request_diff", "acme/widget", 7) in fake_github.calls


async def test_get_issue_round_trip(fake_github: FakeGitHubClient) -> None:
    from draftly.tools.github.get_issue import get_issue

    result = await get_issue("acme", "widget", 3)

    assert result["number"] == 1
    assert ("get_issue", "acme/widget", 3) in fake_github.calls


async def test_create_comment_round_trip(fake_github: FakeGitHubClient) -> None:
    from draftly.tools.github.create_comment import create_comment

    result = await create_comment("acme", "widget", 5, "looks good")

    assert result["body"] == "looks good"
    assert len(fake_github.comments) == 1


def test_tool_schemas_render() -> None:
    from draftly.tools.github.create_comment import create_comment
    from draftly.tools.github.get_diff import get_diff
    from draftly.tools.github.get_issue import get_issue

    for fn in (get_diff, get_issue, create_comment):
        spec = getattr(fn, "tool_spec", None) or tool_decorator(fn).tool_spec
        assert spec["name"]
        assert "description" in spec or "desc" in str(spec).lower()


async def test_github_search_code_round_trip(fake_github: FakeGitHubClient) -> None:
    from draftly.tools.github.search_code import github_search_code

    result = await github_search_code("acme", "widget", "def login", limit=5)

    assert result[0]["path"] == "auth.py"
    assert ("search_code", "acme/widget", "def login", 5) in fake_github.calls


async def test_github_read_file_round_trip(fake_github: FakeGitHubClient) -> None:
    from draftly.tools.github.read_file import github_read_file

    result = await github_read_file("acme", "widget", "auth.py", "main")

    assert "Login helpers" in result
    assert ("get_file_contents", "acme", "widget", "auth.py", "main") in fake_github.calls


async def test_github_get_tree_round_trip(fake_github: FakeGitHubClient) -> None:
    from draftly.tools.github.get_tree import github_get_tree

    result = await github_get_tree("acme", "widget", "main")

    assert result[0]["path"] == "auth.py"
    assert ("get_tree", "acme", "widget", "main") in fake_github.calls
