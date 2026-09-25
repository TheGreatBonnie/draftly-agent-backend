"""Unit tests for GitHub client extensions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.integrations.github.auth import GitHubAuth
from draftly.integrations.github.client import GitHubClient


def _client() -> GitHubClient:
    """Client with a stub token so construction works offline."""
    return GitHubClient(auth=GitHubAuth(token="stub-token"))


@pytest.mark.asyncio
async def test_get_tree_returns_recursive_entries():
    client = _client()
    mock_response = AsyncMock()
    mock_response.json.return_value = {
        "tree": [
            {"path": "README.md", "type": "blob", "sha": "abc123"},
            {"path": "docs/guide.md", "type": "blob", "sha": "def456"},
        ],
        "truncated": False,
    }
    mock_response.raise_for_status = AsyncMock()

    with patch.object(
        client,
        "_request",
        new_callable=AsyncMock,
        return_value=mock_response.json.return_value,
    ):
        result = await client.get_tree("owner", "repo", "main", "token123")
        assert len(result) == 2
        assert result[0]["path"] == "README.md"


@pytest.mark.asyncio
async def test_get_tree_bounded_bounds_entries_and_flags_truncation():
    """An oversized tree must never come back whole: the model cannot fit it in
    one turn (run e1e96f90: 18 ``github_get_tree`` results were replaced with a
    max-tokens error and the writer never learned the repo layout)."""
    client = _client()
    tree = {
        "tree": [
            {"path": f"docs/page-{index}.md", "type": "blob", "sha": f"s{index}"}
            for index in range(10)
        ],
        "truncated": False,
    }

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=tree):
        page = await client.get_tree_bounded("owner", "repo", "main", max_entries=3)

    assert [entry["path"] for entry in page["entries"]] == [
        "docs/page-0.md",
        "docs/page-1.md",
        "docs/page-2.md",
    ]
    assert page["truncated"] is True


@pytest.mark.asyncio
async def test_get_tree_bounded_filters_by_path_prefix():
    """``path_prefix`` narrows the listing to one subtree instead of the repo."""
    client = _client()
    tree = {
        "tree": [
            {"path": "README.md", "type": "blob", "sha": "a"},
            {"path": "docs/guide.md", "type": "blob", "sha": "b"},
            {"path": "docs/api/oauth.md", "type": "blob", "sha": "c"},
            {"path": "src/app.py", "type": "blob", "sha": "d"},
        ],
        "truncated": False,
    }

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=tree):
        page = await client.get_tree_bounded("owner", "repo", "main", path_prefix="docs")

    assert [entry["path"] for entry in page["entries"]] == [
        "docs/guide.md",
        "docs/api/oauth.md",
    ]
    assert page["truncated"] is False


@pytest.mark.asyncio
async def test_get_tree_bounded_walk_prunes_and_caps_within_the_prefix():
    """A truncated recursive response is completed by walking subtrees, but only
    the subtrees that can contribute to ``path_prefix``, and the result stays
    inside the entry budget."""
    client = _client()
    root = {
        "tree": [
            {"path": "docs", "type": "tree", "sha": "t1"},
            {"path": "src", "type": "tree", "sha": "t2"},
        ],
        "truncated": True,
    }
    docs = {
        "tree": [
            {"path": "docs/guide.md", "type": "blob", "sha": "b1"},
            {"path": "docs/api/oauth.md", "type": "blob", "sha": "b2"},
        ],
        "truncated": False,
    }
    requests: list[str] = []

    async def fake_request(method: str, path: str, **kwargs: object) -> dict:
        requests.append(path)
        return root if path.endswith("/trees/main") else docs

    with patch.object(client, "_request", side_effect=fake_request):
        page = await client.get_tree_bounded(
            "owner", "repo", "main", path_prefix="docs", max_entries=2
        )

    assert page["entries"] == [
        {"path": "docs", "type": "tree", "sha": "t1"},
        {"path": "docs/guide.md", "type": "blob", "sha": "b1"},
    ]
    assert page["truncated"] is True
    # The src subtree is never fetched: it cannot contribute to "docs".
    assert not any(path.endswith("/trees/t2") for path in requests)


@pytest.mark.asyncio
async def test_get_tree_bounded_does_not_walk_once_the_budget_is_spent():
    """The entry budget also stops the subtree walk itself — an already-spent
    budget must not trigger more GitHub requests."""
    client = _client()
    root = {
        "tree": [
            {"path": "docs", "type": "tree", "sha": "t1"},
            {"path": "src", "type": "tree", "sha": "t2"},
        ],
        "truncated": True,
    }
    requests: list[str] = []

    async def fake_request(method: str, path: str, **kwargs: object) -> dict:
        requests.append(path)
        return root

    with patch.object(client, "_request", side_effect=fake_request):
        page = await client.get_tree_bounded(
            "owner", "repo", "main", path_prefix="docs", max_entries=1
        )

    assert page["entries"] == [{"path": "docs", "type": "tree", "sha": "t1"}]
    assert page["truncated"] is True
    assert requests == ["/repos/owner/repo/git/trees/main"]


@pytest.mark.asyncio
async def test_get_tree_still_returns_a_plain_list_for_existing_callers():
    """``get_tree`` keeps its unbounded list contract (sync service, onboarding)."""
    client = _client()
    tree = {"tree": [{"path": "README.md", "type": "blob", "sha": "a"}], "truncated": False}

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=tree):
        result = await client.get_tree("owner", "repo", "main", "token123")

    assert result == [{"path": "README.md", "type": "blob", "sha": "a"}]


@pytest.mark.asyncio
async def test_get_file_contents_returns_decoded_string():
    client = _client()
    import base64
    content = "# Hello\n\nWorld."
    encoded = base64.b64encode(content.encode()).decode()
    mock_response = {"content": encoded, "encoding": "base64"}

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=mock_response):
        result = await client.get_file_contents("owner", "repo", "README.md", "main", "token123")
        assert result == content


@pytest.mark.asyncio
async def test_get_repository_forwards_per_call_token():
    client = _client()
    with patch.object(
        client, "_request", new_callable=AsyncMock, return_value={"name": "repo"}
    ) as req:
        result = await client.get_repository("owner/repo", token="token123")
        assert result == {"name": "repo"}
        assert req.await_args.kwargs["token"] == "token123"

        # Default call stays compatible: no explicit override required.
        await client.get_repository("owner/repo")
        assert req.await_args.kwargs.get("token") is None


@pytest.mark.asyncio
async def test_installation_client_uses_installation_token_for_async_requests():
    from draftly.integrations.github import client as client_module

    client = GitHubClient(installation_id=987)
    response = MagicMock()
    response.json.return_value = {"name": "repo"}
    response.raise_for_status = MagicMock()
    http_client = AsyncMock()
    http_client.request.return_value = response
    with patch.object(
        client_module,
        "get_installation_token",
        new_callable=AsyncMock,
        return_value="installation-token",
    ) as token_factory, patch.object(
        client,
        "_client",
        return_value=http_client,
    ):
        result = await client.get_repository("owner/repo")

    assert result == {"name": "repo"}
    token_factory.assert_awaited_once_with(987)
    assert http_client.request.await_args.kwargs["headers"]["Authorization"] == (
        "Bearer installation-token"
    )


@pytest.mark.asyncio
async def test_get_pull_request_diff_uses_installation_token():
    from draftly.integrations.github import client as client_module

    client = GitHubClient(installation_id=987)
    response = MagicMock()
    response.text = "diff --git a/README.md b/README.md"
    response.raise_for_status = MagicMock()
    http_client = AsyncMock()
    http_client.request.return_value = response
    with patch.object(
        client_module,
        "get_installation_token",
        new_callable=AsyncMock,
        return_value="installation-token",
    ) as token_factory, patch.object(
        client,
        "_client",
        return_value=http_client,
    ):
        result = await client.get_pull_request_diff("owner/repo", 42)

    assert result == "diff --git a/README.md b/README.md"
    token_factory.assert_awaited_once_with(987)
    headers = http_client.request.await_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer installation-token"
    assert headers["Accept"] == "application/vnd.github.v3.diff"


@pytest.mark.asyncio
async def test_get_pull_request_diff_forwards_explicit_token():
    client = _client()
    response = MagicMock()
    response.text = "diff --git a/README.md b/README.md"
    response.raise_for_status = MagicMock()
    http_client = AsyncMock()
    http_client.request.return_value = response
    with patch.object(
        client,
        "_client",
        return_value=http_client,
    ):
        result = await client.get_pull_request_diff("owner/repo", 42)

    assert result == "diff --git a/README.md b/README.md"
    headers = http_client.request.await_args.kwargs["headers"]
    # Explicit-token callers keep the auth-token header rather than crashing.
    assert headers["Authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_get_file_contents_skips_large_files():
    client = _client()
    # Simulate a file > 1MB by returning empty content
    with patch.object(
        client,
        "_request",
        new_callable=AsyncMock,
        return_value={"content": "", "encoding": "base64"},
    ):
        result = await client.get_file_contents("owner", "repo", "huge.md", "main", "token123")
        assert result == ""


@pytest.mark.asyncio
async def test_get_last_commit_date_returns_date():
    client = _client()
    mock_data = [{"commit": {"committer": {"date": "2026-08-27T10:30:00Z"}}}]
    with patch.object(client, "_request", new_callable=AsyncMock, return_value=mock_data):
        result = await client.get_last_commit_date("owner", "repo", "README.md", "main", "tok")
    assert result is not None
    assert result.year == 2026
    assert result.month == 8


@pytest.mark.asyncio
async def test_get_last_commit_date_returns_none_on_error():
    client = _client()
    with patch.object(
        client, "_request", new_callable=AsyncMock, side_effect=RuntimeError("rate limit")
    ):
        result = await client.get_last_commit_date("owner", "repo", "README.md", "main", "tok")
    assert result is None
