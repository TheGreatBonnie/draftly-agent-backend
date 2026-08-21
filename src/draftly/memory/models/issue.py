"""Issue memory model."""

from __future__ import annotations

from .base import MemoryItem


class Issue(MemoryItem):
    """A GitHub issue distilled into memory."""

    memory_type: str = "issue"
    number: int | None = None
    repository: str | None = None
    state: str = "open"
