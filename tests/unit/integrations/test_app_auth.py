"""Unit tests for GitHub App auth helpers."""

from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from draftly.integrations.github import app_auth


@pytest.fixture(autouse=True)
def stable_app_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep JWT tests independent of developer-specific ``.env`` values."""
    monkeypatch.setattr(app_auth, "_get_app_id", lambda: "123456")


def _real_pem() -> str:
    """A genuine RSA key: jwt.encode rejects placeholders with InvalidKeyError."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


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


def test_generate_jwt_raises_actionable_error_when_private_key_missing(monkeypatch, tmp_path):
    """A configured-but-absent key path must name the variable, not raise FileNotFoundError.

    Render sets GITHUB_PRIVATE_KEY_PATH to /etc/secrets/github-app-private-key.pem
    without materialising that file, so a bare read_text() surfaced as an opaque
    HTTP 500 from every GitHub App API call.
    """
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(tmp_path / "absent.pem"))
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(app_auth, "_get_private_key_path", lambda: str(tmp_path / "absent.pem"))
    monkeypatch.setattr(app_auth, "_get_private_key_material", lambda: "")

    with pytest.raises(RuntimeError) as exc:
        app_auth.generate_jwt()

    message = str(exc.value)
    assert "GITHUB_PRIVATE_KEY_PATH" in message
    assert "GITHUB_APP_PRIVATE_KEY" in message


def test_generate_jwt_uses_inline_private_key_material(monkeypatch):
    """In-container deployments inject the PEM directly; no file needed."""
    monkeypatch.setattr(app_auth, "_get_private_key_material", _real_pem)

    token = app_auth.generate_jwt()

    assert jwt.get_unverified_header(token)["alg"] == "RS256"


def test_generate_jwt_unescapes_newlines_in_inline_material(monkeypatch):
    """Secrets injected as env vars arrive with literal \\n, not real newlines."""
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", _real_pem().replace("\n", "\\n"))
    monkeypatch.setattr(app_auth, "_get_private_key_path", lambda: "")

    assert jwt.get_unverified_header(app_auth.generate_jwt())["alg"] == "RS256"


def test_generate_jwt_falls_back_to_key_file(monkeypatch, tmp_path):
    """Local/dev and the RQ worker pass a path; that must keep working."""
    key_file = tmp_path / "key.pem"
    key_file.write_text(_real_pem())
    monkeypatch.setattr(app_auth, "_get_private_key_material", lambda: "")
    monkeypatch.setattr(app_auth, "_get_private_key_path", lambda: str(key_file))

    assert jwt.get_unverified_header(app_auth.generate_jwt())["alg"] == "RS256"
