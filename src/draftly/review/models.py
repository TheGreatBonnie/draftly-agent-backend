"""Review domain models (plan §8.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
    """A reviewer's approve/reject decision."""

    model_config = ConfigDict(extra="allow")

    review_id: str
    reviewer_id: str
    approved: bool
    comment: str = ""
    decided_at: datetime | None = None
