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

    def __init__(
        self,
        repository: ReviewsRepository | None = None,
        outcomes_repository: Any | None = None,
    ) -> None:
        self.repository = repository or ReviewsRepository()
        self.outcomes = outcomes_repository
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
            result = await self.approvals.approve(
                review_id=decision.review_id,
                reviewer_id=decision.reviewer_id,
                comment=decision.comment,
            )
        else:
            result = await self.rejections.reject(
                review_id=decision.review_id,
                reviewer_id=decision.reviewer_id,
                comment=decision.comment,
            )
        if self.outcomes is not None:
            record = result.get("review")
            if record is not None:
                try:
                    await self.outcomes.save_outcome(
                        record.org_id,
                        "human_review",
                        record.id,
                        {
                            "decision": record.decision,
                            "comment": record.decision_comment,
                            "run_id": record.thread_id,
                            "workflow": record.workflow,
                        },
                    )
                except Exception:
                    # Review delivery must not fail because learning telemetry is unavailable.
                    pass
        return result

    async def expire_stale(self) -> int:
        expired = await self.queue.expire_stale()
        return len(expired)

    async def resume_review_decision(
        self,
        *,
        review_id: str,
        approved: bool,
        reviewer_id: str,
        comment: str,
        app_state: Any,
        org_id: str | None = None,
    ):
        """Resume the paused workflow and record the decision (plan §9.6).

        Thin wrapper over the shared service so platform routes keep one
        entry point; see ``draftly.review.resume`` for semantics.
        """
        from draftly.review.resume import resume_review_decision as _resume

        return await _resume(
            review_id=review_id,
            approved=approved,
            reviewer_id=reviewer_id,
            comment=comment,
            app_state=app_state,
            org_id=org_id,
        )

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
