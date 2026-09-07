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
