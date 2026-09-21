"""Durable page-scoped documentation workflow contracts.

Immutable artifacts, per-page evaluation results, revision and cross-page
review tasks, and aggregate workflow summaries for the documentation graph.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PageStatus(StrEnum):
    PENDING = "pending"
    WRITING = "writing"
    EVALUATING = "evaluating"
    REVISING = "revising"
    PASSED = "passed"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DocumentArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)
    page_id: str
    path: str
    artifact_id: str
    version: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: str
    status: Literal["sealed"] = "sealed"
    content: str = Field(exclude=True, repr=False)


class MetricResult(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    passed: bool
    blocking: bool
    reason: str


class PageEvaluationResult(BaseModel):
    """Immutable evaluation of one sealed artifact version.

    Mirrors the documentation_page_evaluations status check: an evaluation
    either passes, requests a targeted revision, or escalates to human review.
    """

    page_id: str
    artifact_id: str
    version: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt: int = Field(ge=1)
    status: Literal["passed", "revision_required", "awaiting_human_review"]
    score: float = Field(ge=0.0, le=1.0)
    metrics: list[MetricResult]
    revision_feedback: list[str]


class RevisionTask(BaseModel):
    page_id: str
    path: str
    artifact_id: str
    artifact_version: int = Field(ge=1)
    failed_metrics: list[MetricResult]
    instructions: list[str]
    revision_attempt: int = Field(ge=1)


class CrossPageCorrection(BaseModel):
    code: str
    page_ids: list[str] = Field(min_length=1)
    reason: str


class CrossPageReviewResult(BaseModel):
    passed: bool = False
    corrections: list[CrossPageCorrection] = Field(default_factory=list)


class DocumentationWorkflowResult(BaseModel):
    """Aggregate summary of the settled page evaluations for one run."""

    passed: bool
    ready_for_review: bool
    page_count: int
    passed_page_ids: list[str]
    failed_page_ids: list[str]
    escalated_page_ids: list[str]

    @classmethod
    def from_pages(
        cls, evaluations: Iterable[PageEvaluationResult]
    ) -> DocumentationWorkflowResult:
        pages = [evaluation for evaluation in evaluations if evaluation is not None]
        passed_page_ids = [
            evaluation.page_id
            for evaluation in pages
            if evaluation.status == PageStatus.PASSED.value
        ]
        escalated_page_ids = [
            evaluation.page_id
            for evaluation in pages
            if evaluation.status == PageStatus.AWAITING_HUMAN_REVIEW.value
        ]
        failed_page_ids = [
            evaluation.page_id
            for evaluation in pages
            if evaluation.status
            not in (PageStatus.PASSED.value, PageStatus.AWAITING_HUMAN_REVIEW.value)
        ]
        passed = bool(pages) and len(passed_page_ids) == len(pages)
        ready_for_review = bool(pages) and all(
            evaluation.status
            in (PageStatus.PASSED.value, PageStatus.AWAITING_HUMAN_REVIEW.value)
            for evaluation in pages
        )
        return cls(
            passed=passed,
            ready_for_review=ready_for_review,
            page_count=len(pages),
            passed_page_ids=passed_page_ids,
            failed_page_ids=failed_page_ids,
            escalated_page_ids=escalated_page_ids,
        )


def normalize_page_id(path: str) -> str:
    """Return a repository-relative page id for the given path.

    Rejects absolute paths and any path containing a ``..`` segment.
    """
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"page path must be repository-relative: {path!r}")
    segments = normalized.split("/")
    if ".." in segments:
        raise ValueError(f"page path must not contain '..' segments: {path!r}")
    cleaned = [segment for segment in segments if segment not in ("", ".")]
    if not cleaned:
        raise ValueError(f"page path must name a page: {path!r}")
    return "/".join(cleaned)
