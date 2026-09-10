"""Review domain models (plan §8.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ReviewRequest(BaseModel):
    """A pending human review of a proposed documentation change."""

    model_config = ConfigDict(extra="allow")

    review_id: str
    run_id: str
    workflow: str
    org_id: str = ""
    summary: str = ""
    evaluation: dict[str, Any] = {}
    evidence_count: int = 0
    interrupt_id: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None


class ReviewDecision(BaseModel):
    """A reviewer's decision, including a request for agent revisions.

    ``approved`` remains optional for backwards compatibility with the
    existing Slack, Discord, and API callers. New callers should send the
    explicit ``decision`` value. ``review_id`` defaults to empty because the
    resume route resolves the stored review by ``run_id``; ``comment`` is
    nullable so dashboards can omit it.
    """

    model_config = ConfigDict(extra="allow")

    review_id: str = ""
    reviewer_id: str
    approved: bool | None = None
    decision: Literal["approve", "request_changes", "reject"] | None = None
    comment: str | None = None
    decided_at: datetime | None = None

    def normalized_decision(self) -> Literal["approve", "request_changes", "reject"]:
        if self.decision is not None:
            return self.decision
        if self.approved is not None:
            return "approve" if self.approved else "reject"
        raise ValueError("A review decision is required")
