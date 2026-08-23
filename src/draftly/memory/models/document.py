"""Document memory model."""

from __future__ import annotations

from .base import MemoryItem


class Document(MemoryItem):
    """A documentation page tracked in memory."""

    memory_type: str = "document"
    path: str | None = None
    repository: str | None = None
    title: str | None = None
    heading_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
