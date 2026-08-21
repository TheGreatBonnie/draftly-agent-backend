"""Feedback loop task state."""

from __future__ import annotations

from pydantic import BaseModel


class FeedbackEvent(BaseModel):
    """Normalized feedback surface event (the graph's task JSON)."""

    event_id: str
    project_id: str
    source: str
    source_message_id: str
    normalized_question: str
    answer_status: str  # "answered" | "unanswered" | "partial"
