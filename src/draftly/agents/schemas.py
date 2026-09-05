"""Pydantic schemas shared by Draftly agents and graph nodes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EventClassification(BaseModel):
    """Structured classifier output for an incoming surface event."""

    surface: str = Field(description='"pull_request" | "issue" | "support_question"')
    change_type: str = Field(
        description=(
            '"documentation_only" | "bug_fix" | "new_feature" | "api_change" '
            '| "breaking_change" | "deprecation" | "question" | "other"'
        )
    )
    urgency: str = Field(description='"low" | "medium" | "high"')
    reason: str = Field(description="Short justification for the classification")


class EvidenceBundle(BaseModel):
    """Evidence collected by the context/research agents."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class ImpactAnalysis(BaseModel):
    """Documentation impact analysis for a surface event."""

    action: str = Field(description='"answer" | "update" | "create" | "none"')
    affected_documents: list[str] = Field(default_factory=list)
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


class DocChangePlan(BaseModel):
    """A concrete documentation change plan produced by a writer."""

    repository: str = ""
    branch: str = ""
    files: list[dict[str, Any]] = Field(
        min_length=1,
        description="[{path, content, action: create|update}] - at least one file is required",
    )
    commit_message: str = ""
    summary: str = ""


class ChangelogEntry(BaseModel):
    """A changelog entry for a single release version."""

    version: str = Field(description="Version tag, e.g. v2.0.0")
    date: str = Field(description="ISO 8601 date, e.g. 2026-09-04")
    entries: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "[{category, text}] - category is "
            "Added/Changed/Deprecated/Removed/Fixed/Security"
        ),
    )
    raw_markdown: str = Field(
        description="The complete markdown section to prepend to CHANGELOG.md",
    )


class AnswerDraft(BaseModel):
    """A candidate answer for the support/issue surface."""

    content: str = ""
    sources: list[str] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    """Evaluation output for a generated draft."""

    passed: bool = False
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class DeliveryReceipt(BaseModel):
    """Receipt produced by the delivery node after a successful delivery."""

    delivered_to: str = ""
    surface: str = ""
    reference: str = ""
    status: str = "completed"
