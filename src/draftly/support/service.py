"""Support service (plan §8.7) — support question lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from draftly.delivery.slack import SlackDelivery
from draftly.memory.retrieval import MemoryRetrieval
from draftly.support.answer import SupportAnswerValidator
from draftly.support.classifier import SupportClassifier
from draftly.support.escalation import EscalationService
from draftly.support.models import SupportAnswer, SupportQuestion


class SupportService:
    """Triage → answer → escalate/deliver lifecycle facade."""

    def __init__(
        self,
        classifier: SupportClassifier | None = None,
        validator: SupportAnswerValidator | None = None,
        escalation: EscalationService | None = None,
        memory: MemoryRetrieval | None = None,
        delivery: SlackDelivery | None = None,
    ) -> None:
        self.classifier = classifier or SupportClassifier()
        self.validator = validator or SupportAnswerValidator()
        self.escalation = escalation or EscalationService(delivery=delivery)
        self.memory = memory

    def register_question(
        self,
        *,
        platform: str,
        content: str,
        author: str | None = None,
        channel_id: str | None = None,
        thread_id: str | None = None,
        source_message_id: str | None = None,
        org_id: str | None = None,
    ) -> SupportQuestion:
        question = SupportQuestion(
            question_id=f"q-{uuid.uuid4().hex[:12]}",
            platform=platform,
            content=content,
            author=author,
            channel_id=channel_id,
            thread_id=thread_id,
            source_message_id=source_message_id,
            org_id=org_id,
            timestamp=datetime.now(UTC),
        )
        return self.classifier.triage(question)

    async def validate_answer(
        self,
        *,
        question: SupportQuestion,
        content: str,
        evidence: list[dict[str, Any]] | None = None,
    ) -> SupportAnswer:
        return self.validator.validate(
            question_id=question.question_id,
            content=content,
            evidence=evidence,
        )

    async def handle_answer(
        self,
        *,
        question: SupportQuestion,
        answer: SupportAnswer,
        channel_id: str | None = None,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """Deliver the answer or escalate when confidence is low."""
        if self.escalation.should_escalate(question, answer.confidence):
            result = await self.escalation.escalate(
                question,
                reason=f"confidence {answer.confidence}",
            )
            return {"action": "escalated", **result}

        target_channel = channel_id or question.channel_id
        delivered = None
        if target_channel:
            delivered = await self.escalation.delivery.post_message(
                channel_id=target_channel,
                message=answer.content,
                thread_id=thread_ts or question.thread_id,
            )
        return {"action": "delivered", "delivery": delivered}
