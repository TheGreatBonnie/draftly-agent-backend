"""Candidate models shared by extraction and curation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

CANDIDATE_TYPES = (
    "fact",
    "decision",
    "procedure_pattern",
    "doc_relation",
    "episode_summary",
)


class MemoryCandidate(BaseModel):
    """A unit of potential knowledge awaiting curator judgment."""

    id: str | None = None
    org_id: str | None = None
    candidate_type: str
    payload: dict[str, Any]
    source_type: str | None = None
    source_id: str | None = None
    evidence: list[str] = []
    confidence: float = 0.5
    status: str = "pending"
