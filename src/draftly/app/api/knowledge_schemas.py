"""Typed response contracts for Knowledge read APIs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class KnowledgeStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_VERIFICATION = "needs-verification"
    STALE = "stale"


class KnowledgeListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    entity: str | None
    description: str | None
    status: KnowledgeStatus
    importance: float | None
    confidence: float | None
    created_at: datetime | None
    updated_at: datetime | None
    namespace: str
    memory_type: str
    similarity: float | None = None


class KnowledgePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeListItem]
    total: int
    next_cursor: str | None


class KnowledgeStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    verified: int
    needs_verification: int
    stale: int


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_type: str
    source_id: str | None = None
    source_url: str | None = None
    repository: str | None = None
    commit_sha: str | None = None
    evidence: str | None = None


class KnowledgeLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    relationship: str
    source_memory_id: str
    target_memory_id: str
    confidence: float | None = None


class KnowledgeFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    feedback_type: str
    source: str | None = None
    score: float | None = None
    comment: str | None = None
    created_at: datetime | None = None


class KnowledgeDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    entity: str | None
    description: str | None
    status: KnowledgeStatus
    importance: float | None
    confidence: float | None
    created_at: datetime | None
    updated_at: datetime | None
    sources: list[KnowledgeSource]
    related: list[KnowledgeLink]
    feedback: list[KnowledgeFeedback]


class KnowledgeSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    repository: str | None
    item_count: int
    evidence_count: int
    last_seen_at: datetime | None


class KnowledgeGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    status: KnowledgeStatus
    memory_type: str


class KnowledgeGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    relationship: str
    confidence: float | None


class KnowledgeGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[KnowledgeGraphNode]
    edges: list[KnowledgeGraphEdge]


class KnowledgeTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    item_count: int
    verified_count: int
    stale_count: int


class KnowledgeTopics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeTopic]


class KnowledgeEmbeddingStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_items: int
    embedded_items: int
    coverage_percent: float
    models: list[str]
    last_embedded_at: datetime | None


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    items: list[KnowledgeListItem]
    total: int
