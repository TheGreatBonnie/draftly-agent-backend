"""Solution memory model."""

from __future__ import annotations

from .base import MemoryItem


class Solution(MemoryItem):
    """A validated answer linked to a question."""

    memory_type: str = "solution"
    question_id: str | None = None
    resolution_status: str = "open"
