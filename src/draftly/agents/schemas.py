"""Pydantic schemas shared by Draftly agents and graph nodes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from draftly.agents.taxonomy import (
    CHANGE_TYPES,
    DOCA_ACTIONS,
    SURFACES,
    URGENCY_LEVELS,
)


def _enum_description(values: tuple[str, ...]) -> str:
    """Render an ``"a" | "b"`` field description from a vocabulary tuple."""
    return " | ".join(f'"{v}"' for v in values)


class EventClassification(BaseModel):
    """Structured classifier output for an incoming surface event."""

    surface: str = Field(description=_enum_description(SURFACES))
    change_type: str = Field(description=_enum_description(CHANGE_TYPES))
    urgency: str = Field(description=_enum_description(URGENCY_LEVELS))
    reason: str = Field(description="Short justification for the classification")


class EvidenceItem(BaseModel):
    """A single evidence item: what it points at and what it covers.

    LLM research output is validated as ``EvidenceItem`` (Strands builds a
    structured-output tool named after the model class). ``id``/``url`` hold
    the concrete source locator, ``topic`` the coverage topic the draft must
    address, and ``excerpt`` a short quote. Unknown keys survive validation
    so legacy freeform payloads keep working.
    """

    id: str = ""
    url: str = ""
    topic: str = ""
    excerpt: str = ""

    model_config = ConfigDict(extra="allow")


class EvidenceBundle(BaseModel):
    """Evidence collected by the context/research agents."""

    items: list[EvidenceItem] = Field(default_factory=list)
    summary: str = ""


class ImpactAnalysis(BaseModel):
    """Documentation impact analysis for a surface event."""

    action: str = Field(description=_enum_description(DOCA_ACTIONS))
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


class NotifyReceipt(BaseModel):
    """Draft of the PR notify comment produced by the notify agent.

    The notify agent is strictly a draft composer (no tools): it reads the
    impact verdict from ``From impact:`` and returns this receipt. A
    deterministic ``notify_post`` node posts ``body`` as a PR comment when
    ``should_notify`` is true.
    """

    should_notify: bool = False
    kind: str = Field(
        default="",
        description='One of "gap_detected" | "no_gap"',
    )
    body: str = Field(
        default="",
        description="Author-facing markdown comment explaining Draftly's work for this PR",
    )
