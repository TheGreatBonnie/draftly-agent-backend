"""Canonical models for draft-only content production."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContentChannel(StrEnum):
    BLOG = "blog"
    LINKEDIN = "linkedin"
    X = "x"


class ContentPackageStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ContentRequest(BaseModel):
    """Validated input for every content-production source."""

    model_config = ConfigDict(extra="forbid")

    org_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    source_event_type: Literal[
        "pull_request", "release", "documentation", "manual_brief", "feedback_gap"
    ]
    source_title: str = Field(min_length=1)
    source_summary: str = Field(min_length=1)
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_gap_id: str | None = None
    source_evidence: list[dict[str, Any]] = Field(default_factory=list)
    requested_channels: list[ContentChannel] = Field(min_length=1)
    audience: str = Field(min_length=1)
    tone: str = Field(min_length=1)

    @field_validator("requested_channels")
    @classmethod
    def unique_channels(cls, value: list[ContentChannel]) -> list[ContentChannel]:
        if len(set(value)) != len(value):
            raise ValueError("requested_channels must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_feedback_provenance(self) -> ContentRequest:
        if self.source_event_type == "feedback_gap":
            if not self.source_gap_id:
                raise ValueError("feedback_gap source requires source_gap_id")
            if not self.source_feedback_ids:
                raise ValueError("feedback_gap source requires source_feedback_ids")
        return self


class ContentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    package_id: str
    revision_number: int = Field(ge=1)
    reason: Literal["initial", "request_changes", "regeneration"]
    reviewer_comment: str | None = None
    created_by_run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContentVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    package_id: str
    channel: ContentChannel
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    status: ContentPackageStatus = ContentPackageStatus.DRAFT
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    revision_id: str | None = None


class ContentPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    org_id: str
    repository_id: str
    source_event_id: str
    source_event_type: str
    status: ContentPackageStatus
    brief: str = Field(min_length=1)
    source_evidence: list[dict[str, Any]] = Field(default_factory=list)
    variants: list[ContentVariant] = Field(default_factory=list)
    workflow_run_id: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_gap_id: str | None = None
    revisions: list[ContentRevision] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_revision_order(self) -> ContentPackage:
        numbers = [revision.revision_number for revision in self.revisions]
        if numbers != sorted(set(numbers)):
            raise ValueError("revisions must have unique, monotonic revision_number values")
        return self
