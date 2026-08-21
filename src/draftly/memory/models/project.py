"""Project memory model."""

from __future__ import annotations

from .base import MemoryItem


class Project(MemoryItem):
    """Repository/project level context."""

    memory_type: str = "project"
    repository: str | None = None
    default_branch: str = "main"
