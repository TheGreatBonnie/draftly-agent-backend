"""Conversation memory model."""

from __future__ import annotations

from .base import MemoryItem


class Conversation(MemoryItem):
    """A support conversation excerpt."""

    memory_type: str = "conversation"
    platform: str | None = None
    thread_id: str | None = None
