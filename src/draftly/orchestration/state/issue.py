"""Issue surface task state."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class IssueEvent(BaseModel):
    """Normalized issue surface event (the graph's task JSON)."""

    event_id: str
    event_type: str  # "issues.opened" | "issues.reopened"
    project_id: str
    repository: str
    actor: str
    issue: dict[str, Any]  # {number, title, body, labels, ...}
