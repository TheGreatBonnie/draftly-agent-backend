"""Feedback domain models (plan §8.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FeedbackItem(BaseModel):
    """A single feedback signal (question, reaction, comment)."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    org_id: str | None = None
    platform: str = "slack"
    topic: str | None = None
    content: str
    author: str | None = None
    channel: str | None = None
    source_message_id: str | None = None
    source_event_id: str | None = None
    source_url: str | None = None
    category: str = "question"
    sentiment: str = "neutral"
    timestamp: datetime | None = None


class FeedbackCluster(BaseModel):
    """Questions grouped around one unresolved topic."""

    model_config = ConfigDict(extra="allow")

    topic: str
    items: list[FeedbackItem] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.items)


class DocumentationGapCandidate(BaseModel):
    """A cluster promoted to a documentation-gap candidate."""

    model_config = ConfigDict(extra="allow")

    topic: str
    occurrences: int
    severity: float = 0.5
    platforms: list[str] = Field(default_factory=list)
    sample_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = {}


class ContentOpportunity(BaseModel):
    """A feedback gap that can be handed to the content pipeline."""

    org_id: str
    gap_id: str
    topic: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    recommended_channels: list[str] = Field(default_factory=lambda: ["blog", "linkedin", "x"])
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = "recurring feedback gap"
