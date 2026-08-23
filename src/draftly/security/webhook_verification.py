"""Webhook signature verification (plan §8.8).

Unified verification for GitHub (HMAC-SHA256), Slack (v0 HMAC) and
Discord (Ed25519). Delegates to the integration-level verifiers where
they exist.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import structlog

logger = structlog.get_logger(__name__)

SLACK_MAX_AGE_SECONDS = 300


class WebhookVerificationError(Exception):
    """Raised when a webhook fails signature verification."""


class WebhookVerifier:
    """Verify raw webhook bodies against platform signatures."""

    def __init__(
        self,
        *,
        github_secret: str | None = None,
        slack_signing_secret: str | None = None,
        discord_public_key: str | None = None,
    ) -> None:
        self.github_secret = github_secret
        self.slack_signing_secret = slack_signing_secret
        self.discord_public_key = discord_public_key

    # --------------------------------------------------------------
    # GitHub: sha256=<hex> HMAC of the raw body
    # --------------------------------------------------------------

    def verify_github(self, body: bytes, signature: str) -> bool:
        if not self.github_secret:
            raise WebhookVerificationError("GitHub webhook secret is not configured")
        expected = hmac.new(
            self.github_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature or "")

    # --------------------------------------------------------------
    # Slack: v0:<timestamp>:<body> HMAC, replay window enforced
    # --------------------------------------------------------------

    def verify_slack(
        self,
        body: bytes,
        timestamp: str,
        signature: str,
    ) -> bool:
        if not self.slack_signing_secret:
            raise WebhookVerificationError("Slack signing secret is not configured")
        try:
            ts_int = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise WebhookVerificationError("Invalid Slack timestamp") from exc
        if abs(time.time() - ts_int) > SLACK_MAX_AGE_SECONDS:
            logger.warning("slack_webhook_replay_rejected")
            return False

        basestring = f"v0:{timestamp}:".encode() + body
        expected = hmac.new(
            self.slack_signing_secret.encode(),
            basestring,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"v0={expected}", signature or "")

    # --------------------------------------------------------------
    # Discord: Ed25519 over <timestamp><body>
    # --------------------------------------------------------------

    def verify_discord(
        self,
        body: bytes,
        timestamp: str,
        signature_hex: str,
    ) -> bool:
        if not self.discord_public_key:
            raise WebhookVerificationError("Discord public key is not configured")
        try:
            from nacl.exceptions import BadSignatureError  # ty: ignore[unresolved-import]
            from nacl.signing import VerifyKey  # ty: ignore[unresolved-import]
        except ImportError as exc:  # pragma: no cover - dependency pinned
            raise WebhookVerificationError(
                "PyNaCl not installed; cannot verify Discord webhooks"
            ) from exc

        try:
            key = VerifyKey(bytes.fromhex(self.discord_public_key))
            message = timestamp.encode() + body
            key.verify(message, bytes.fromhex(signature_hex or ""))
            return True
        except (BadSignatureError, ValueError):
            return False
