"""Unit tests for GitHub client extensions."""

from unittest.mock import AsyncMock, patch

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

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=mock_response.json.return_value):
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
async def test_get_file_contents_skips_large_files():
    client = _client()
    # Simulate a file > 1MB by returning empty content
    with patch.object(client, "_request", new_callable=AsyncMock, return_value={"content": "", "encoding": "base64"}):
        result = await client.get_file_contents("owner", "repo", "huge.md", "main", "token123")
        assert result == ""
