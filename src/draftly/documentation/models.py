"""Documentation domain models (plan §8.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentInfo(BaseModel):
    """A documentation page under management."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    repository: str
    path: str
    title: str = ""
    content: str = ""
    document_type: str = "markdown"
    metadata: dict[str, Any] = {}
    updated_at: datetime | None = None


class DocumentationGap(BaseModel):
    """An identified hole in the documentation."""

    model_config = ConfigDict(extra="allow")

    topic: str
    source: str = "support"
    occurrences: int = 1
    severity: float = 0.5
    sample_questions: list[str] = Field(default_factory=list)
    related_paths: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """Outcome of link/freshness validation for one document."""

    model_config = ConfigDict(extra="allow")

    path: str
    valid: bool = True
    broken_links: list[str] = Field(default_factory=list)
    stale_days: int | None = None
    issues: list[str] = Field(default_factory=list)
