"""Unit tests for GitHub App auth helpers."""

from unittest.mock import AsyncMock, patch

import pytest

from draftly.integrations.github import app_auth


@pytest.mark.asyncio
async def test_build_installation_client_uses_minted_token():
    """Client must be constructed with the installation token — never env PAT."""
    with (
        patch.object(
            app_auth,
            "get_installation_token",
            new=AsyncMock(return_value="ghs_live"),
        ) as mint,
        patch("draftly.integrations.github.auth.GitHubAuth") as auth_cls,
        patch("draftly.integrations.github.client.GitHubClient") as client_cls,
    ):
        client = await app_auth.build_installation_client("156354594")

    mint.assert_awaited_once_with(156354594)
    auth_cls.assert_called_once_with(token="ghs_live")
    client_cls.assert_called_once_with(auth=auth_cls.return_value)
    assert client is client_cls.return_value
