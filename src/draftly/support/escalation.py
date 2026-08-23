"""Escalation service (plan §8.7) — escalate unclear questions."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.delivery.slack import SlackDelivery
from draftly.support.models import SupportQuestion

logger = structlog.get_logger(__name__)

ESCALATION_CHANNEL = "support-escalation"


class EscalationService:
    """Route low-confidence or high-urgency questions to humans."""

    def __init__(
        self,
        delivery: SlackDelivery | None = None,
        *,
        confidence_threshold: float = 0.4,
        escalation_channel: str = ESCALATION_CHANNEL,
    ) -> None:
        self.delivery = delivery or SlackDelivery()
        self.confidence_threshold = confidence_threshold
        self.escalation_channel = escalation_channel

    def should_escalate(
        self,
        question: SupportQuestion,
        confidence: float | None = None,
    ) -> bool:
        if question.urgency == "high":
            return True
        return confidence is not None and confidence < self.confidence_threshold

    async def escalate(
        self,
        question: SupportQuestion,
        reason: str = "low confidence",
    ) -> dict[str, Any]:
        message = (
            f":rotating_light: *Support escalation* ({reason})\n"
            f"*Platform:* {question.platform}\n"
            f"*From:* {question.author or 'unknown'}\n"
            f"*Category:* {question.category} / urgency={question.urgency}\n"
            f"> {question.content[:500]}"
        )
        result = await self.delivery.post_message(
            channel_id=self.escalation_channel,
            message=message,
        )
        logger.info(
            "support_escalated question_id=%s reason=%s",
            question.question_id,
            reason,
        )
        return {"escalated": True, "delivery": result}
