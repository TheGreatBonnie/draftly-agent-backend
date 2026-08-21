"""Rejection handling (plan §8.3) — record decision, feed back to graph."""

from __future__ import annotations

import logging
from typing import Any

from draftly.persistence.repositories.reviews import ReviewsRepository

logger = logging.getLogger(__name__)


class RejectionHandler:
    """Reject a pending review; the graph cancels the delivery node.

    Per the ReviewGate contract, a rejection response makes the gate set
    ``event.cancel_node``, which surfaces as a RuntimeError from
    ``invoke_async`` on resume. The workflow runner catches it and marks
    the run failed with the reviewer comment.
    """

    def __init__(self, repository: ReviewsRepository | None = None) -> None:
        self.repository = repository or ReviewsRepository()

    async def reject(
        self,
        *,
        review_id: str,
        reviewer_id: str,
        comment: str = "",
    ) -> dict[str, Any]:
        record = await self.repository.record_decision(
            review_id=review_id,
            reviewer_id=reviewer_id,
            decision="rejected",
            comment=comment or None,
        )
        interrupt_id = (record.tool_args or {}).get("interrupt_id")
        logger.info(
            "review_rejected review_id=%s run_id=%s",
            review_id,
            record.thread_id,
        )
        return {
            "review": record,
            "resume": {
                "run_id": record.thread_id,
                "interrupt_id": interrupt_id,
                "response": {"approved": False, "comment": comment},
            },
            "expected_outcome": "delivery_cancelled",
        }
