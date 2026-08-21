"""Review service (plan §8.3) — queue, decisions, notifications."""

from __future__ import annotations

from typing import Any

from draftly.persistence.repositories.reviews import ReviewsRepository
from draftly.review.approvals import ApprovalHandler
from draftly.review.models import ReviewDecision, ReviewRequest
from draftly.review.policies import ReviewPolicy
from draftly.review.queue import ReviewQueue
from draftly.review.rejection import RejectionHandler


class ReviewService:
    """High-level review API: queue inspection + decisions."""

    def __init__(self, repository: ReviewsRepository | None = None) -> None:
        self.repository = repository or ReviewsRepository()
        self.queue = ReviewQueue(self.repository)
        self.approvals = ApprovalHandler(self.repository)
        self.rejections = RejectionHandler(self.repository)

    def policy(self, name: str) -> ReviewPolicy:
        return ReviewPolicy(name)

    async def list_pending(self, org_id: str | None = None) -> list[ReviewRequest]:
        records = await self.queue.pending(org_id)
        return [self._to_request(r) for r in records]

    async def get(self, review_id: str) -> ReviewRequest | None:
        record = await self.queue.get(review_id)
        return self._to_request(record) if record else None

    async def get_by_run_id(self, run_id: str) -> ReviewRequest | None:
        """Find the pending review interrupt for a workflow run."""
        record = await self.repository.get_pending_by_run_id(run_id)
        return self._to_request(record) if record else None

    async def decide(self, decision: ReviewDecision) -> dict[str, Any]:
        """Apply an approve/reject decision and return resume info."""
        if decision.approved:
            return await self.approvals.approve(
                review_id=decision.review_id,
                reviewer_id=decision.reviewer_id,
                comment=decision.comment,
            )
        return await self.rejections.reject(
            review_id=decision.review_id,
            reviewer_id=decision.reviewer_id,
            comment=decision.comment,
        )

    async def expire_stale(self) -> int:
        expired = await self.queue.expire_stale()
        return len(expired)

    @staticmethod
    def _to_request(record: Any) -> ReviewRequest:
        tool_args = record.tool_args or {}
        metadata = record.metadata or {}
        reason = tool_args.get("reason") or {}
        if isinstance(reason, dict):
            summary = str(reason.get("summary", ""))
            evaluation = reason.get("evaluation") or {}
            evidence_count = int(reason.get("evidence_count", 0))
        else:
            summary = str(reason)
            evaluation = {}
            evidence_count = 0
        return ReviewRequest(
            review_id=record.id,
            run_id=record.thread_id,
            workflow=record.workflow,
            org_id=record.org_id,
            summary=summary,
            evaluation=evaluation,
            evidence_count=evidence_count,
            interrupt_id=tool_args.get("interrupt_id") or metadata.get("interrupt_id"),
            created_at=record.created_at,
            expires_at=record.expires_at,
        )
