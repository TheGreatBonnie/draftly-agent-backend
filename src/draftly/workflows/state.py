"""Workflow state (plan §7.2) — replaces the old WorkflowResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class WorkflowStatus(StrEnum):
    """Terminal and in-flight workflow outcomes."""

    PENDING = "pending"
    RUNNING = "running"
    DUPLICATE = "duplicate"
    PENDING_REVIEW = "pending_review"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class WorkflowState:
    """Everything a run produces, keyed by its run_id (= event_id)."""

    run_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    surface: str | None = None
    event: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    errors: list[str] = field(default_factory=list)
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    def finish(self, status: WorkflowStatus) -> WorkflowState:
        self.status = status
        self.finished_at = datetime.now(UTC).isoformat()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "surface": self.surface,
            "errors": list(self.errors),
            "interrupts": list(self.interrupts),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
