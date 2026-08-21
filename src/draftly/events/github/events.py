"""GitHub domain events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class GitHubEvent(BaseModel):
    """A normalized GitHub webhook event."""

    model_config = ConfigDict(extra="allow")

    event_id: str
    event_type: str
    repository: str
    occurred_at: datetime | None = None
    actor: str | None = None
    payload: dict[str, Any] = {}


class GitHubIssueEvent(GitHubEvent):
    """A GitHub issue-related event."""


class GitHubPullRequestEvent(GitHubEvent):
    """A GitHub pull request event."""


class GitHubReleaseEvent(GitHubEvent):
    """A GitHub release event."""
