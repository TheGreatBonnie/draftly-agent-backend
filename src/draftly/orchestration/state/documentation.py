"""Task state schemas for pull request / issue documentation workflows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PullRequestEvent(BaseModel):
    """Normalized pull request surface event (the graph's task JSON)."""

    event_id: str
    event_type: str  # "pull_request.opened" | "pull_request.synchronize"
    project_id: str
    repository: str
    actor: str
    pull_request: dict[str, Any]  # {number, sha, title, body, changed_files, ...}


class IssueEvent(BaseModel):
    """Normalized issue surface event (the graph's task JSON)."""

    event_id: str
    event_type: str  # "issues.opened" | "issues.reopened"
    project_id: str
    repository: str
    actor: str
    issue: dict[str, Any]  # {number, title, body, labels, ...}
