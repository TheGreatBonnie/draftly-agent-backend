from __future__ import annotations

import os
import time
from pathlib import Path
from typing import cast

import httpx
import jwt
import structlog

from draftly.app.config import get_settings

logger = structlog.get_logger()


def _get_app_id() -> str:
    app_id = get_settings().github_app_id or os.getenv("GITHUB_APP_ID", "")
    return app_id if app_id else ""


def _get_private_key_path() -> str:
    path = get_settings().github_private_key_path or os.getenv("GITHUB_PRIVATE_KEY_PATH", "")
    return path if path else ""


def _get_webhook_secret() -> str:
    return os.getenv("GITHUB_WEBHOOK_SECRET", "")


def generate_jwt() -> str:
    """Generate a JWT signed with the App's private key for GitHub API authentication."""
    private_key_path = Path(_get_private_key_path())
    private_key = private_key_path.read_text()

    payload = {
        "iat": int(time.time()) - 60,
        "exp": int(time.time()) + (10 * 60),
        "iss": _get_app_id(),
    }
    return cast(str, jwt.encode(payload, private_key, algorithm="RS256"))


async def get_installation_token(installation_id: int) -> str:
    """Exchange JWT for temporary repository-specific access token."""
    jwt_token = generate_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        logger.info("installation_token_obtained", installation_id=installation_id)
        return cast(str, token_data["token"])


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Validate webhook authenticity using HMAC SHA256."""
    if not signature:
        return False

    secret = _get_webhook_secret()
    if not secret:
        return False

    from draftly.security.webhook_verification import (
        WebhookVerificationError,
        WebhookVerifier,
    )

    try:
        return WebhookVerifier(github_secret=secret).verify_github(
            payload, signature
        )
    except WebhookVerificationError:
        return False


async def get_installation_info(installation_id: int) -> dict:
    """Look up installation details (account/org name) from GitHub API."""
    jwt_token = generate_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return cast(dict, resp.json())


async def get_installation_repositories(token: str) -> list[dict]:
    """List repositories accessible by the installation."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/installation/repositories",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return cast(list[dict], data.get("repositories", []))


async def post_issue_comment(
    owner: str, repo: str, issue_number: int, body: str, token: str
) -> dict:
    """Post a comment on a GitHub issue using installation token."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json={"body": body}, timeout=10)
        resp.raise_for_status()
        logger.info("issue_comment_posted", owner=owner, repo=repo, issue=issue_number)
        return cast(dict, resp.json())


async def add_issue_labels(
    owner: str, repo: str, issue_number: int, labels: list[str], token: str
) -> dict:
    """Add labels to a GitHub issue."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/labels"

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json={"labels": labels}, timeout=10)
        resp.raise_for_status()
        logger.info("issue_labels_added", owner=owner, repo=repo, issue=issue_number, labels=labels)
        return cast(dict, resp.json())
