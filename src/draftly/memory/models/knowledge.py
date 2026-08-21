"""Knowledge memory model."""

from __future__ import annotations

from .base import MemoryItem


class Knowledge(MemoryItem):
    """Curated knowledge used to ground answers."""

    memory_type: str = "knowledge"
    topic: str | None = None
    source_quality: float = 0.5
