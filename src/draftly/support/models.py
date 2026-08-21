"""Support domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SupportMessage(BaseModel):
    """A single support message across any platform."""

    model_config = ConfigDict(extra="allow")

    id: str
    platform: str
    channel_id: str | None = None
    channel_name: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    content: str = ""
    thread_id: str | None = None
    timestamp: datetime | None = None
    url: str | None = None
    raw: dict[str, Any] | None = None
    org_id: str | None = None


class SupportThread(BaseModel):
    """A support thread (conversation) across any platform."""

    model_config = ConfigDict(extra="allow")

    id: str
    platform: str
    channel_id: str | None = None
    channel_name: str | None = None
    root_message_id: str | None = None
    messages: list[SupportMessage] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] | None = None
    org_id: str | None = None


class SupportQuestion(BaseModel):
    """A triaged support question (plan §8.7)."""

    model_config = ConfigDict(extra="allow")

    question_id: str
    platform: str
    content: str
    author: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    source_message_id: str | None = None
    category: str = "question"
    urgency: str = "normal"
    org_id: str | None = None
    timestamp: datetime | None = None


class SupportAnswer(BaseModel):
    """A generated, validated answer to a support question."""

    model_config = ConfigDict(extra="allow")

    answer_id: str
    question_id: str
    content: str
    confidence: float = 0.0
    citations: list[str] = []
    grounded: bool = False
    metadata: dict[str, Any] = {}
    created_at: datetime | None = None
