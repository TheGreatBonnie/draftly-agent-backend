"""Question memory model."""

from __future__ import annotations

from .base import MemoryItem


class Question(MemoryItem):
    """A user question captured from a support surface."""

    memory_type: str = "question"
    topic: str | None = None
    source_message_id: str | None = None
