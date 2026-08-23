"""Approval handling (plan §8.3) — record decision, resume the graph."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.persistence.repositories.reviews import ReviewsRepository

logger = structlog.get_logger(__name__)


class ApprovalHandler:
    """Approve a pending review and resume the paused workflow run."""

    def __init__(
        self,
        repository: ReviewsRepository | None = None,
        session_factory: Any = None,
    ) -> None:
        self.repository = repository or ReviewsRepository()
        self.session_factory = session_factory

    async def approve(
        self,
        *,
        review_id: str,
        reviewer_id: str,
        comment: str = "",
    ) -> dict[str, Any]:
        """Record approval; returns resume instructions for the run."""
        record = await self.repository.record_decision(
            review_id=review_id,
            reviewer_id=reviewer_id,
            decision="approved",
            comment=comment or None,
        )
        interrupt_id = (record.tool_args or {}).get("interrupt_id")
        return {
            "review": record,
            "resume": {
                "run_id": record.thread_id,
                "interrupt_id": interrupt_id,
                "response": {"approved": True, "comment": comment},
            },
        }

    def build_resume_input(self, interrupt_id: str, response: dict) -> list[dict]:
        """Format the strands multi-turn resume input."""
        return [
            {
                "interruptResponse": {
                    "interruptId": interrupt_id,
                    "response": response,
                }
            }
        ]
