from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from draftly.events.github.events import (
    GitHubEvent,
    GitHubIssueEvent,
    GitHubPullRequestEvent,
    GitHubReleaseEvent,
)


class GitHubWebhookHandler:
    """
    Converts GitHub webhook payloads into Draftly domain events.
    """

    def __init__(
        self,
        secret: str | None = None,
    ):
        self.secret = secret or os.getenv(
            "GITHUB_WEBHOOK_SECRET"
        )

    def verify_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        if not self.secret:
            raise RuntimeError(
                "GITHUB_WEBHOOK_SECRET is not configured."
            )

        expected = hmac.new(
            self.secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        expected_signature = f"sha256={expected}"

        return hmac.compare_digest(
            expected_signature,
            signature,
        )

    def parse(
        self,
        payload: dict[str, Any],
        event_name: str,
    ) -> GitHubEvent | None:
        repository = payload.get(
            "repository",
            {},
        ).get(
            "full_name"
        )

        if not repository:
            return None

        actor = (
            payload.get("sender", {})
            .get("login")
        )

        occurred_at = datetime.now(
            UTC
        )

        event_id = str(uuid.uuid4())

        if event_name == "issues":
            action = payload.get("action")

            supported = {
                "opened",
                "edited",
                "closed",
                "reopened",
            }

            if action not in supported:
                return None

            return GitHubIssueEvent(
                event_id=event_id,
                repository=repository,
                event_type=f"issues.{action}",
                occurred_at=occurred_at,
                actor=actor,
                payload=payload,
            )

        if event_name == "pull_request":
            action = payload.get("action")

            supported = {
                "opened",
                "edited",
                "closed",
            }

            if action not in supported:
                return None

            if (
                action == "closed"
                and payload.get(
                    "pull_request",
                    {},
                ).get("merged")
            ):
                event_type = (
                    "pull_request.merged"
                )
            else:
                event_type = (
                    f"pull_request.{action}"
                )

            return GitHubPullRequestEvent(
                event_id=event_id,
                repository=repository,
                event_type=event_type,
                occurred_at=occurred_at,
                actor=actor,
                payload=payload,
            )

        if event_name == "release":
            action = payload.get("action")

            supported = {
                "created",
                "published",
                "edited",
            }

            if action not in supported:
                return None

            return GitHubReleaseEvent(
                event_id=event_id,
                repository=repository,
                event_type=f"release.{action}",
                occurred_at=occurred_at,
                actor=actor,
                payload=payload,
            )

        return None
