"""Baseline snapshot after successful documentation sync."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class BaselineSnapshot(BaseModel):
    """Snapshot record after successful sync."""

    commit_sha: str
    repository: str
    document_count: int
    section_count: int
    chunk_count: int
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def create_baseline(
    commit_sha: str,
    repository: str,
    document_count: int,
    section_count: int,
    chunk_count: int,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> BaselineSnapshot:
    """Create a new baseline snapshot."""
    return BaselineSnapshot(
        commit_sha=commit_sha,
        repository=repository,
        document_count=document_count,
        section_count=section_count,
        chunk_count=chunk_count,
        include=include or [],
        exclude=exclude or [],
    )
