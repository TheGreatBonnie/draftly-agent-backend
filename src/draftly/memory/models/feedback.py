"""Feedback memory model."""

from __future__ import annotations

from .base import MemoryItem


class Feedback(MemoryItem):
    """Feedback about delivered answers or docs."""

    memory_type: str = "feedback"
    target_id: str | None = None
    sentiment: str = "neutral"
