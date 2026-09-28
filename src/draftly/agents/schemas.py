"""Pydantic schemas shared by Draftly agents and graph nodes."""

from __future__ import annotations

from pathlib import PurePath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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


#: Field-length ceilings for ``EvidenceItem``.
#:
#: Strands' ``structured_output`` path is non-streaming, so an
#: ``EvidenceBundle`` must materialise inside ONE response against ONE
#: per-call ``max_tokens``. With no ceiling, a model that pastes file contents
#: into ``excerpt`` spends the whole budget on evidence and never emits the
#: tool call's closing brace — strands reports ``max_tokens`` and strands'
#: own recovery discards the partial message (run ``ce8ea540``; the same shape
#: truncated the writer's 679-tool-call JSON earlier).
#:
#: The ceilings live in the schema, not in a prompt, so they reach the model as
#: ``maxLength`` in the generated tool definition and it can budget against
#: them. They are deliberately far above a real citation.
EVIDENCE_EXCERPT_MAX_CHARS = 2_000
EVIDENCE_TOPIC_MAX_CHARS = 500
EVIDENCE_URL_MAX_CHARS = 2_000
EVIDENCE_ID_MAX_CHARS = 500


#: Field ceilings for ``ImpactAnalysis``, the documentation plan.
#:
#: Same reasoning as ``EVIDENCE_*_MAX_CHARS`` above, one node earlier: the plan
#: is model-authored, lands in ONE response, and is the one payload that had no
#: ceilings at all. Run ``9ab7a0a0`` lost the ``impact`` node to ``max_tokens``
#: with the ``impact_analysis`` tool call cut off mid-JSON, and strands'
#: recovery discards the partial message -- so the node produced nothing.
#:
#: The counts are the important half. ``tasks`` is a multiplier over each task's
#: own evidence, so an unbounded list is quadratic in the worst case, and a
#: model that enumerates the repository rather than planning a change is the
#: realistic way to get there. They are set far above a real single-PR plan
#: (run ``9ab7a0a0`` produced 9 evidence items) because a rejected plan costs
#: the whole node: these catch the runaway, not the merely large.
IMPACT_RATIONALE_MAX_CHARS = 4_000
IMPACT_MAX_TASKS = 50
IMPACT_MAX_AFFECTED_DOCUMENTS = 200
IMPACT_MAX_EVIDENCE = 50
IMPACT_DOCUMENT_MAX_CHARS = 500

#: A single path/anchor string. ``max_length`` on a ``list[str]`` bounds the
#: *count*, not the items, so the item ceiling needs its own type -- which also
#: puts ``maxLength`` on the item in the generated tool schema, where the model
#: can see it.
ImpactRef = Annotated[str, StringConstraints(max_length=IMPACT_DOCUMENT_MAX_CHARS)]


class EvidenceItem(BaseModel):
    """A single evidence item: what it points at and what it covers.

    LLM research output is validated as ``EvidenceItem`` (Strands builds a
    structured-output tool named after the model class). When the evidence is
    page-scoped, ``id`` is the documentation page it supports and the writer is
    required to cite that page; ``topic`` is the coverage topic the draft must
    address and ``excerpt`` a short quote. ``url`` is a source locator for
    material that is not a documentation page. Unknown keys survive validation
    so legacy freeform payloads keep working.
    """

    id: str = Field(
        default="",
        max_length=EVIDENCE_ID_MAX_CHARS,
        description=(
            "Path or URL of the documentation page this evidence supports. "
            "For page-scoped evidence this MUST be exactly equal to the task's "
            "path, and the draft must cite it. Use url instead for source "
            "material that is not a documentation page."
        ),
    )
    url: str = Field(default="", max_length=EVIDENCE_URL_MAX_CHARS)
    topic: str = Field(default="", max_length=EVIDENCE_TOPIC_MAX_CHARS)
    excerpt: str = Field(default="", max_length=EVIDENCE_EXCERPT_MAX_CHARS)

    model_config = ConfigDict(extra="allow")


class EvidenceBundle(BaseModel):
    """Evidence collected by the context/research agents."""

    items: list[EvidenceItem] = Field(default_factory=list)
    summary: str = ""


class DocumentationTask(BaseModel):
    """One page/bundle to write or update (a single isolated writer unit)."""

    id: str
    path: str
    action: str = "update"  # only "update" | "create" (validated by plan_tasks/downstream)
    reason: str = ""
    related_symbols: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description=(
            "Page-scoped evidence for this task. Attach at least one item and "
            "set each item's id to exactly this task's path. A task with no "
            "evidence escalates to human review without being evaluated."
        ),
    )
    requirements: list[str] = Field(default_factory=list)
    bundle_id: str | None = None
    # Set by the workflow from the original event, never trusted from the plan.
    repository: str | None = None
    head_sha: str | None = None


class ReviewCorrection(BaseModel):
    """One targeted page correction from the global review pass."""

    task_id: str
    path: str
    instructions: list[str] = Field(default_factory=list)


class ReviewVerdict(BaseModel):
    """Global cross-page coherence verdict."""

    verdict: Literal["clean", "correct"] = "clean"
    corrections: list[ReviewCorrection] = Field(default_factory=list)


class ImpactAnalysis(BaseModel):
    """Documentation impact analysis for a surface event."""

    action: str = Field(description=_enum_description(DOCA_ACTIONS))
    affected_documents: list[ImpactRef] = Field(
        default_factory=list,
        max_length=IMPACT_MAX_AFFECTED_DOCUMENTS,
    )
    rationale: str = Field(default="", max_length=IMPACT_RATIONALE_MAX_CHARS)
    evidence: list[ImpactRef] = Field(default_factory=list, max_length=IMPACT_MAX_EVIDENCE)
    tasks: list[DocumentationTask] = Field(
        default_factory=list,
        max_length=IMPACT_MAX_TASKS,
    )

    @model_validator(mode="after")
    def _validate_task_paths(self) -> ImpactAnalysis:
        seen: set[str] = set()
        for task in self.tasks:
            path = task.path.strip()
            if not path:
                raise ValueError(f"task {task.id!r} has an empty path")
            candidate = PurePath(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(
                    f"task {task.id!r} path must be relative and within the repo: {path!r}"
                )
            if path in seen:
                raise ValueError(f"duplicate task path: {path}")
            seen.add(path)
        return self


class DocChangePlan(BaseModel):
    """A concrete documentation change plan produced by a writer.

    Metadata-only: ``files`` carries ``[{path, action: create|update}]`` and
    NEVER the file bytes. Content is written by the writer's draft tools into
    the draft store (``draftly/tools/documentation/drafts.py``) and read back
    from ``draft_revisions``/``draft_chunks`` by the evaluator, review gate,
    and delivery agent. Inlining content here is what overflowed the streaming
    parser and produced `failed to parse tool input json`; the validator below
    makes that impossible at the schema boundary.
    """

    repository: str = ""
    branch: str = ""
    files: list[dict[str, Any]] = Field(
        min_length=1,
        description="metadata only; stream content via the draft tools",
    )
    commit_message: str = ""
    summary: str = ""

    @model_validator(mode="after")
    def _reject_inline_content(self) -> DocChangePlan:
        for entry in self.files:
            if not isinstance(entry, dict):
                raise ValueError(
                    f"DocChangePlan file entry must be an object {{path, action}}, "
                    f"got {type(entry).__name__}"
                )
            path = entry.get("path")
            if not path or not str(path).strip():
                raise ValueError("DocChangePlan file entry requires a non-empty 'path'")
            if "content" in entry:
                raise ValueError(
                    "DocChangePlan files must not carry inline content: "
                    "stream file bytes with start_draft/append_chunk/finalize_draft "
                    "into the draft store instead (content lives in draft_chunks, "
                    "never in the plan JSON)"
                )
        return self


class ChangelogEntry(BaseModel):
    """A changelog entry for a single release version."""

    version: str = Field(description="Version tag, e.g. v2.0.0")
    date: str = Field(description="ISO 8601 date, e.g. 2026-09-04")
    entries: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "[{category, text}] - category is Added/Changed/Deprecated/Removed/Fixed/Security"
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
