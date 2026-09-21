"""Handlers for durable, page-scoped documentation workflow tasks.

Each handler accepts one persisted :class:`WorkflowTask` and returns compact,
JSON-safe output for the executor. Markdown bodies stay in ``DraftRepository``;
task inputs carry only page plans, evaluation metadata, and targeted review
instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

import structlog

from draftly.agents.documentation.draft_scope import (
    DraftScope,
    reset_draft_scope,
    set_draft_scope,
)
from draftly.agents.documentation.writer import WriterFactory
from draftly.agents.schemas import DocumentationTask
from draftly.orchestration.nodes.evaluate import (
    _evidence_has_signals,
    compute_page_metrics,
)
from draftly.orchestration.nodes.rubric_grader import RubricGrader
from draftly.orchestration.page_workflow.models import (
    DocumentArtifact,
    PageEvaluationResult,
    PageStatus,
    normalize_page_id,
)
from draftly.orchestration.page_workflow.repository import (
    MAX_AUTOMATIC_EVALUATION_ATTEMPTS,
    PageState,
    PageWorkflowRepository,
    RevisionSchedule,
    WorkflowTask,
)
from draftly.persistence.repositories.drafts import DraftFile, DraftRepository

logger = structlog.get_logger(__name__)

MAX_PAGE_EVALUATION_ATTEMPTS = MAX_AUTOMATIC_EVALUATION_ATTEMPTS
CROSS_PAGE_REVIEW_TASK_ID = "cross-page-review"

_FIRST_HEADING = re.compile(r"^#(?!#)\s+(.+)$", re.MULTILINE)
_LINKS = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_REFERENCES_HEADER = re.compile(r"(?m)^##+ *References\b")
_REFERENCE_NUMBER = re.compile(r"(?m)^\s*\d+\.\s")


def render_task_prompt(task: DocumentationTask) -> str:
    """One isolated writer prompt for a single page/bundle.

    Scoped to this task's path, action, reason, symbols, requirements, and
    path-matched evidence — other pages' evidence deliberately absent so
    attention never competes across pages.
    """
    lines = [
        f"Documentation task: {task.id}",
        f"Path: {task.path}",
        f"Action: {task.action}",
        f"Reason: {task.reason or 'see evidence'}",
    ]
    if task.related_symbols:
        lines.append("Related symbols: " + ", ".join(task.related_symbols))
    if task.requirements:
        lines.append("Requirements (do not document anything else):")
        lines += [f"- {req}" for req in task.requirements]
    if task.evidence:
        lines.append("Evidence scoped to this page:")
        lines += [
            f"- {item.id}: {item.model_dump(exclude={'id'}, exclude_none=True)}"
            for item in task.evidence
        ]
    return "\n".join(lines)


async def page_summaries(
    drafts_repo: Any | None, run_id: str | None, tasks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deterministic, LLM-free page summaries from sealed store content."""
    if drafts_repo is None:
        return []
    by_path = {f.path: f.content for f in await drafts_repo.get_latest(run_id=run_id) or []}
    summaries: list[dict[str, Any]] = []
    for task in tasks:
        path = str(task.get("path") or "")
        content = by_path.get(path, "")
        if not content:
            continue
        summaries.append(
            {
                "path": path,
                "headings": _FIRST_HEADING.findall(content)[:8],
                "links": _LINKS.findall(_REFERENCES_HEADER.split(content)[0])[:10],
                "first_paragraph": _first_paragraph(content),
                "references": len(_REFERENCE_NUMBER.findall(content)),
                "char_length": len(content),
            }
        )
    return summaries


def _first_paragraph(content: str) -> str:
    body = _FIRST_HEADING.sub("", content, count=1).strip()
    for para in body.split("\n\n"):
        if para.strip():
            return " ".join(para.split())[:400]
    return ""


def render_review_prompt(
    tasks: list[dict[str, Any]], summaries: list[dict[str, Any]], summary: str
) -> str:
    lines = [
        "Review the documentation change set for cross-page coherence.",
        f"Change-set summary: {summary or '(none)'}",
        "",
        "Generated pages:",
    ]
    for task in tasks:
        lines.append(f"- {task['path']} ({task['action']}, ok={task['ok']})")
    lines.append("")
    lines.append("Compact per-page summaries:")
    for blob in summaries:
        lines.append("- " + json.dumps(blob, sort_keys=True))
    lines.append("")
    lines.append(
        "Return verdict 'clean' when coherent, or 'correct' with targeted "
        "per-page instructions keyed by task_id. Never rewrite pages."
    )
    return "\n".join(lines)


def render_revision_prompt(
    task: DocumentationTask,
    artifact: DocumentArtifact,
    evaluation: PageEvaluationResult | None,
    *,
    reviewer_instructions: list[str] | None = None,
) -> str:
    """Render a revision prompt containing only one page's failure context."""
    lines = [
        f"Revise documentation task: {task.id}",
        f"Path: {task.path}",
        "Modify only this assigned page and preserve correct existing content.",
        "",
        f"Current artifact (version {artifact.version}):",
        artifact.content,
    ]
    if evaluation is not None:
        lines.extend(["", "Failed metrics:"])
        failed = [
            metric
            for metric in evaluation.metrics
            if metric.blocking and not metric.passed
        ]
        lines.extend(
            f"- {metric.name}: {metric.score:.2f} "
            f"(threshold {metric.threshold:.2f}) — {metric.reason}"
            for metric in failed
        )
        if evaluation.revision_feedback:
            lines.extend(["", "Evaluator feedback:"])
            lines.extend(f"- {item}" for item in evaluation.revision_feedback)
    instructions = list(reviewer_instructions or [])
    if instructions:
        lines.extend(["", "Cross-page reviewer instructions:"])
        lines.extend(f"- {item}" for item in instructions)
    if task.evidence:
        lines.extend(["", "Evidence scoped to this page:"])
        lines.extend(
            f"- {item.id}: {item.model_dump(exclude={'id'}, exclude_none=True)}"
            for item in task.evidence
        )
    return "\n".join(lines)


def _canonical_page_id(value: str) -> str:
    try:
        normalized = normalize_page_id(value)
    except ValueError as exc:
        raise ValueError(f"page ID must be canonical: {value!r}") from exc
    if normalized != value:
        raise ValueError(f"page ID must be canonical: {value!r}")
    return normalized


def _document_task(workflow_task: WorkflowTask) -> DocumentationTask:
    raw = workflow_task.input_data.get("task")
    if not isinstance(raw, dict):
        raise ValueError(f"task {workflow_task.task_id!r} is missing its page plan")
    page = DocumentationTask.model_validate(raw)
    if workflow_task.page_id is None:
        raise ValueError(f"task {workflow_task.task_id!r} is missing page_id")
    _canonical_page_id(workflow_task.page_id)
    if page.id != workflow_task.page_id or page.path != workflow_task.page_id:
        raise ValueError(
            f"task {workflow_task.task_id!r} page plan does not match page_id "
            f"{workflow_task.page_id!r}"
        )
    return page


def _artifact(page_id: str, file: DraftFile) -> DocumentArtifact:
    if file.version is None or file.content_hash is None:
        raise ValueError(f"page {page_id!r} artifact is missing versioned identity")
    return DocumentArtifact(
        page_id=page_id,
        path=file.path,
        artifact_id=file.artifact_id,
        version=file.version,
        content_hash=file.content_hash,
        action=file.action,
        content=file.content,
    )


async def _current_artifact(
    drafts_repo: DraftRepository,
    *,
    run_id: str,
    page_id: str,
    expected_version: int | None = None,
    expected_artifact_id: str | None = None,
) -> DocumentArtifact:
    file = await drafts_repo.get_path_latest(run_id=run_id, path=page_id)
    if file is None:
        raise ValueError(f"page {page_id!r} has no sealed artifact")
    if not (file.content or "").strip():
        raise ValueError(f"page {page_id!r} sealed artifact is empty")
    artifact = _artifact(page_id, file)
    if expected_version is not None and artifact.version != expected_version:
        raise ValueError(
            f"page {page_id!r} expected artifact version {expected_version}, "
            f"found {artifact.version}"
        )
    if expected_artifact_id is not None and artifact.artifact_id != expected_artifact_id:
        raise ValueError(
            f"page {page_id!r} expected artifact {expected_artifact_id!r}, "
            f"found {artifact.artifact_id!r}"
        )
    return artifact


class PageWriterHandler:
    """Invoke one fresh writer and promote exactly one sealed artifact."""

    def __init__(
        self,
        *,
        writer_factory: WriterFactory,
        drafts_repo: DraftRepository,
        page_repository: PageWorkflowRepository,
        limits: Any = None,
    ) -> None:
        self.writer_factory = writer_factory
        self.drafts_repo = drafts_repo
        self.page_repository = page_repository
        self.limits = limits

    async def __call__(self, workflow_task: WorkflowTask) -> dict[str, Any]:
        page = _document_task(workflow_task)
        version = workflow_task.artifact_version
        if version is None:
            raise ValueError(f"write task {workflow_task.task_id!r} has no version")

        prepare = getattr(self.drafts_repo, "prepare_versioned_write", None)
        existing_file = (
            await prepare(
                run_id=workflow_task.run_id,
                path=page.id,
                version=version,
            )
            if prepare is not None
            else None
        )
        if existing_file is not None and existing_file.version == version:
            existing = _artifact(page.id, existing_file)
            if not existing.content.strip():
                raise ValueError(f"page {page.id!r} sealed artifact is empty")
            await self.page_repository.record_artifact(
                run_id=workflow_task.run_id,
                page_id=page.id,
                artifact_id=existing.artifact_id,
                version=version,
            )
            return existing.model_dump(exclude={"content"})

        raw_evaluation = workflow_task.input_data.get("evaluation")
        evaluation = (
            PageEvaluationResult.model_validate(raw_evaluation)
            if isinstance(raw_evaluation, dict)
            else None
        )
        reviewer_instructions = [
            str(item)
            for item in workflow_task.input_data.get("reviewer_instructions", [])
            if str(item).strip()
        ]
        if evaluation is not None or reviewer_instructions:
            previous = await _current_artifact(
                self.drafts_repo,
                run_id=workflow_task.run_id,
                page_id=page.id,
                expected_version=evaluation.version if evaluation is not None else None,
                expected_artifact_id=(
                    evaluation.artifact_id if evaluation is not None else None
                ),
            )
            prompt = render_revision_prompt(
                page,
                previous,
                evaluation,
                reviewer_instructions=reviewer_instructions,
            )
        else:
            prompt = render_task_prompt(page)

        agent = self.writer_factory.create(page)
        invocation_state = {
            "run_id": workflow_task.run_id,
            "project_id": workflow_task.org_id,
            "page_id": page.id,
            "artifact_version": version,
        }
        token = set_draft_scope(
            DraftScope(
                run_id=workflow_task.run_id,
                org_id=workflow_task.org_id,
                generation=version,
                version=version,
                assigned_page_id=page.id,
            )
        )
        try:
            kwargs: dict[str, Any] = {}
            if self.limits is not None:
                kwargs["limits"] = self.limits
            await agent.invoke_async(
                prompt,
                invocation_state=invocation_state,
                **kwargs,
            )
        finally:
            reset_draft_scope(token)

        artifact = await _current_artifact(
            self.drafts_repo,
            run_id=workflow_task.run_id,
            page_id=page.id,
            expected_version=version,
        )
        await self.page_repository.record_artifact(
            run_id=workflow_task.run_id,
            page_id=page.id,
            artifact_id=artifact.artifact_id,
            version=version,
        )
        return artifact.model_dump(exclude={"content"})


class PageEvaluatorHandler:
    """Evaluate one immutable artifact and selectively schedule its revision."""

    def __init__(
        self,
        *,
        rubric_grader: RubricGrader,
        drafts_repo: DraftRepository,
        page_repository: PageWorkflowRepository,
        max_attempts: int = MAX_PAGE_EVALUATION_ATTEMPTS,
    ) -> None:
        self.rubric_grader = rubric_grader
        self.drafts_repo = drafts_repo
        self.page_repository = page_repository
        self.max_attempts = max_attempts
        if self.max_attempts > MAX_AUTOMATIC_EVALUATION_ATTEMPTS:
            raise ValueError("max_attempts cannot exceed the shared automatic budget of 3")

    async def __call__(self, workflow_task: WorkflowTask) -> dict[str, Any]:
        page = _document_task(workflow_task)
        version = workflow_task.artifact_version
        if version is None:
            raise ValueError(f"evaluate task {workflow_task.task_id!r} has no version")
        attempt = int(workflow_task.input_data.get("attempt") or version)
        if attempt < 1 or attempt > self.max_attempts:
            raise ValueError(
                f"automatic evaluation attempt must be between 1 and {self.max_attempts}"
            )
        human_guided = bool(workflow_task.input_data.get("human_guided"))
        if human_guided and not workflow_task.input_data.get("revision_comment"):
            raise ValueError("human-guided revision requires a review comment")
        artifact = await _current_artifact(
            self.drafts_repo,
            run_id=workflow_task.run_id,
            page_id=page.id,
            expected_version=version,
        )
        evidence = workflow_task.input_data.get("evidence")
        if evidence is None:
            evidence = [item.model_dump() for item in page.evidence]
        if not isinstance(evidence, list):
            raise ValueError("page evidence must be a list")
        normalized_evidence = [
            item
            for item in evidence
            if isinstance(item, dict) and _evidence_has_signals(item)
        ]
        metrics = compute_page_metrics(normalized_evidence, artifact.content)
        quality = metrics[-1]

        feedback = [
            metric.reason
            for metric in metrics
            if metric.blocking and not metric.passed
        ]
        if not normalized_evidence:
            status = PageStatus.AWAITING_HUMAN_REVIEW.value
            feedback = ["No page-scoped evidence is available; human review required"]
        elif quality.passed:
            status = PageStatus.PASSED.value
            feedback = []
        elif human_guided:
            # A human-guided revision is a single reviewer-directed pass: it
            # never schedules another automatic revision. Feedback degrades to
            # the judge's reasons (best effort) and the page pauses again at
            # the review gate.
            try:
                grade = await self.rubric_grader.grade(
                    draft=artifact.content,
                    evidence=normalized_evidence,
                )
                for reason in grade.reasons:
                    if reason and reason not in feedback:
                        feedback.append(reason)
            except Exception:
                logger.warning(
                    "rubric_grader_failed",
                    run_id=workflow_task.run_id,
                    page_id=page.id,
                    exc_info=True,
                )
            status = PageStatus.AWAITING_HUMAN_REVIEW.value
        else:
            # Best-effort LLM judge (mirror of the legacy evaluate node): an
            # unavailable or degraded model must not crash the workflow, so
            # feedback degrades to the deterministic gate's own reasons.
            try:
                grade = await self.rubric_grader.grade(
                    draft=artifact.content,
                    evidence=normalized_evidence,
                )
                for reason in grade.reasons:
                    if reason and reason not in feedback:
                        feedback.append(reason)
            except Exception:
                logger.warning(
                    "rubric_grader_failed",
                    run_id=workflow_task.run_id,
                    page_id=page.id,
                    exc_info=True,
                )
            status = (
                PageStatus.AWAITING_HUMAN_REVIEW.value
                if attempt >= self.max_attempts
                else "revision_required"
            )

        result = PageEvaluationResult(
            page_id=page.id,
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            content_hash=artifact.content_hash,
            attempt=attempt,
            status=status,
            score=quality.score,
            metrics=metrics,
            revision_feedback=feedback,
        )
        await self.page_repository.record_evaluation(
            result,
            run_id=workflow_task.run_id,
            org_id=workflow_task.org_id,
        )

        if status == "revision_required":
            await self._schedule_revision(workflow_task, page, result)
        elif status == PageStatus.PASSED.value:
            await self._schedule_review_if_ready(workflow_task, page)
        return result.model_dump()

    async def _schedule_revision(
        self,
        workflow_task: WorkflowTask,
        page: DocumentationTask,
        result: PageEvaluationResult,
    ) -> None:
        reviewer_instructions = list(
            workflow_task.input_data.get("reviewer_instructions") or []
        )
        await self.page_repository.schedule_revisions(
            run_id=workflow_task.run_id,
            org_id=workflow_task.org_id,
            revisions=[
                RevisionSchedule(
                    page_id=page.id,
                    source_artifact_id=result.artifact_id,
                    source_version=result.version,
                    attempt=result.attempt + 1,
                    write_input={
                        "task": page.model_dump(),
                        "evaluation": result.model_dump(),
                        "reviewer_instructions": reviewer_instructions,
                    },
                    evaluate_input={
                        "task": page.model_dump(),
                        "evidence": [item.model_dump() for item in page.evidence],
                        "attempt": result.attempt + 1,
                        "reviewer_instructions": reviewer_instructions,
                    },
                )
            ],
        )

    async def _schedule_review_if_ready(
        self,
        workflow_task: WorkflowTask,
        page: DocumentationTask,
    ) -> None:
        states = await self.page_repository.get_page_states(run_id=workflow_task.run_id)
        if not states or any(state.status != PageStatus.PASSED.value for state in states):
            return
        if any(
            state.latest_artifact_id is None or state.latest_version < 1
            for state in states
        ):
            raise ValueError("passed pages must have accepted artifact identities")
        snapshot = {
            state.page_id: {
                "artifact_id": state.latest_artifact_id,
                "version": state.latest_version,
                "content_hash": (
                    await _current_artifact(
                        self.drafts_repo,
                        run_id=workflow_task.run_id,
                        page_id=state.page_id,
                        expected_version=state.latest_version,
                        expected_artifact_id=state.latest_artifact_id,
                    )
                ).content_hash,
            }
            for state in states
        }
        digest = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        review_id = f"{CROSS_PAGE_REVIEW_TASK_ID}:{digest}"
        await self.page_repository.enqueue_task(
            run_id=workflow_task.run_id,
            task_id=review_id,
            org_id=workflow_task.org_id,
            task_type="cross_page_review",
            dependencies=[workflow_task.task_id],
            input_data={
                "last_page_task": page.model_dump(),
                "artifact_snapshot": snapshot,
            },
        )


class CrossPageReviewHandler:
    """Review accepted artifacts and schedule only named page corrections."""

    def __init__(
        self,
        *,
        reviewer_factory: Callable[[], Any],
        drafts_repo: DraftRepository,
        page_repository: PageWorkflowRepository,
        max_attempts: int = MAX_PAGE_EVALUATION_ATTEMPTS,
    ) -> None:
        self.reviewer_factory = reviewer_factory
        self.drafts_repo = drafts_repo
        self.page_repository = page_repository
        self.max_attempts = max_attempts
        if self.max_attempts > MAX_AUTOMATIC_EVALUATION_ATTEMPTS:
            raise ValueError("max_attempts cannot exceed the shared automatic budget of 3")

    async def __call__(self, workflow_task: WorkflowTask) -> dict[str, Any]:
        states = await self.page_repository.get_page_states(run_id=workflow_task.run_id)
        snapshot = workflow_task.input_data.get("artifact_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        replaying = bool(states) and all(
            state.status == PageStatus.PASSED.value
            or (
                state.status == PageStatus.REVISING.value
                and isinstance(snapshot.get(state.page_id), dict)
                and snapshot[state.page_id].get("artifact_id")
                == state.latest_artifact_id
                and snapshot[state.page_id].get("version") == state.latest_version
            )
            for state in states
        )
        if not replaying:
            raise ValueError("cross-page review starts only after every page has passed")
        for state in states:
            _canonical_page_id(state.page_id)
            if state.path != state.page_id:
                raise ValueError(
                    f"page path must match canonical page ID {state.page_id!r}"
                )
            if state.latest_artifact_id is None or state.latest_version < 1:
                raise ValueError(
                    f"passed page {state.page_id!r} has no accepted artifact identity"
                )

        artifacts: dict[str, DocumentArtifact] = {}
        for state in states:
            artifacts[state.page_id] = await _current_artifact(
                self.drafts_repo,
                run_id=workflow_task.run_id,
                page_id=state.page_id,
                expected_version=state.latest_version,
                expected_artifact_id=state.latest_artifact_id,
            )
            expected = snapshot.get(state.page_id)
            if isinstance(expected, dict) and expected.get("content_hash") not in (
                None,
                artifacts[state.page_id].content_hash,
            ):
                raise ValueError(
                    f"review snapshot for {state.page_id!r} is no longer current"
                )
        rows = [
            {
                "task_id": state.page_id,
                "path": state.path,
                "action": state.action,
                "ok": True,
            }
            for state in states
        ]
        summaries = await page_summaries(
            self.drafts_repo,
            workflow_task.run_id,
            rows,
        )
        reviewer = self.reviewer_factory()
        if reviewer is None:
            raise ValueError("cross-page reviewer is unavailable")
        response = await reviewer.invoke_async(
            render_review_prompt(rows, summaries, "accepted page artifacts"),
            invocation_state={
                "run_id": workflow_task.run_id,
                "project_id": workflow_task.org_id,
            },
        )
        verdict = getattr(response, "structured_output", None)
        if verdict is None:
            raise ValueError("cross-page reviewer returned no verdict")

        corrections = list(getattr(verdict, "corrections", None) or [])
        verdict_name = str(getattr(verdict, "verdict", "") or "")
        if verdict_name == "clean":
            if corrections:
                raise ValueError("clean cross-page verdict cannot contain corrections")
            return {"passed": True, "corrected_page_ids": []}
        if verdict_name != "correct" or not corrections:
            raise ValueError("cross-page corrections must name known page IDs")

        known = {state.page_id: state for state in states}
        validated: list[tuple[PageState, DocumentationTask, list[str]]] = []
        corrected: list[str] = []
        for correction in corrections:
            page_id = str(getattr(correction, "task_id", "") or "")
            path = str(getattr(correction, "path", "") or "")
            if page_id not in known or path != known[page_id].path:
                raise ValueError("cross-page corrections must name known page IDs")
            if page_id in corrected:
                continue
            corrected.append(page_id)
            state = known[page_id]
            next_attempt = state.evaluation_attempt + 1
            if next_attempt > self.max_attempts:
                raise ValueError(
                    f"automatic evaluation attempt cannot exceed {self.max_attempts}"
                )
            validated.append(
                (
                    state,
                    await self._load_page_plan(workflow_task.run_id, state),
                    [
                        str(item)
                        for item in getattr(correction, "instructions", []) or []
                        if str(item).strip()
                    ],
                )
            )
        await self.page_repository.schedule_revisions(
            run_id=workflow_task.run_id,
            org_id=workflow_task.org_id,
            revisions=[
                RevisionSchedule(
                    page_id=state.page_id,
                    source_artifact_id=str(state.latest_artifact_id),
                    source_version=state.latest_version,
                    attempt=state.evaluation_attempt + 1,
                    write_input={
                        "task": page.model_dump(),
                        "reviewer_instructions": instructions,
                    },
                    evaluate_input={
                        "task": page.model_dump(),
                        "evidence": [item.model_dump() for item in page.evidence],
                        "attempt": state.evaluation_attempt + 1,
                        "reviewer_instructions": instructions,
                    },
                )
                for state, page, instructions in validated
            ],
        )
        return {"passed": False, "corrected_page_ids": corrected}

    async def _load_page_plan(
        self,
        run_id: str,
        state: PageState,
    ) -> DocumentationTask:
        get_tasks = getattr(self.page_repository, "get_tasks", None)
        if get_tasks is not None:
            for task in await get_tasks(run_id=run_id):
                raw = task.input_data.get("task")
                if task.page_id == state.page_id and isinstance(raw, dict):
                    return DocumentationTask.model_validate(raw)
        return DocumentationTask(
            id=state.page_id,
            path=state.path,
            action=state.action,
            reason="Apply cross-page review correction",
        )


__all__ = [
    "CrossPageReviewHandler",
    "PageEvaluatorHandler",
    "PageWriterHandler",
    "compute_page_metrics",
    "render_revision_prompt",
]
