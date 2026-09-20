"""Handlers for durable, page-scoped documentation workflow tasks.

Each handler accepts one persisted :class:`WorkflowTask` and returns compact,
JSON-safe output for the executor. Markdown bodies stay in ``DraftRepository``;
task inputs carry only page plans, evaluation metadata, and targeted review
instructions.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from draftly.agents.documentation.draft_scope import (
    DraftScope,
    reset_draft_scope,
    set_draft_scope,
)
from draftly.agents.documentation.writer import WriterFactory
from draftly.agents.schemas import DocumentationTask
from draftly.orchestration.nodes.evaluate import compute_page_metrics
from draftly.orchestration.nodes.fan_out import render_task_prompt
from draftly.orchestration.nodes.review import page_summaries, render_review_prompt
from draftly.orchestration.nodes.rubric_grader import RubricGrader
from draftly.orchestration.page_workflow.models import (
    DocumentArtifact,
    PageEvaluationResult,
    PageStatus,
)
from draftly.orchestration.page_workflow.repository import (
    PageState,
    PageWorkflowRepository,
    WorkflowTask,
)
from draftly.persistence.repositories.drafts import DraftFile, DraftRepository

MAX_PAGE_EVALUATION_ATTEMPTS = 3
CROSS_PAGE_REVIEW_TASK_ID = "cross-page-review"


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


def _document_task(workflow_task: WorkflowTask) -> DocumentationTask:
    raw = workflow_task.input_data.get("task")
    if not isinstance(raw, dict):
        raise ValueError(f"task {workflow_task.task_id!r} is missing its page plan")
    page = DocumentationTask.model_validate(raw)
    if workflow_task.page_id is None:
        raise ValueError(f"task {workflow_task.task_id!r} is missing page_id")
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


async def _set_page_status(
    repository: PageWorkflowRepository,
    *,
    run_id: str,
    page_id: str,
    status: str,
) -> None:
    database = getattr(repository, "database", None)
    if database is None:
        return
    await database.execute(
        """
        UPDATE documentation_page_states
           SET status = $3, updated_at = now()
         WHERE run_id = $1 AND page_id = $2
        """,
        run_id,
        page_id,
        status,
    )


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

    async def __call__(self, workflow_task: WorkflowTask) -> dict[str, Any]:
        page = _document_task(workflow_task)
        version = workflow_task.artifact_version
        if version is None:
            raise ValueError(f"evaluate task {workflow_task.task_id!r} has no version")
        attempt = int(workflow_task.input_data.get("attempt") or version)
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
        normalized_evidence = [item for item in evidence if isinstance(item, dict)]
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
        else:
            grade = await self.rubric_grader.grade(
                draft=artifact.content,
                evidence=normalized_evidence,
            )
            for reason in grade.reasons:
                if reason and reason not in feedback:
                    feedback.append(reason)
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
        await self._project_page_state(workflow_task.run_id, result)

        if status == "revision_required":
            await self._schedule_revision(workflow_task, page, result)
        elif status == PageStatus.PASSED.value:
            await self._schedule_review_if_ready(workflow_task, page)
        return result.model_dump()

    async def _project_page_state(
        self,
        run_id: str,
        result: PageEvaluationResult,
    ) -> None:
        database = getattr(self.page_repository, "database", None)
        if database is None:
            return
        reason = (
            "; ".join(result.revision_feedback)
            if result.status == PageStatus.AWAITING_HUMAN_REVIEW.value
            else None
        )
        await database.execute(
            """
            UPDATE documentation_page_states
               SET status = $3,
                   evaluation_attempt = $4,
                   escalation_reason = $5,
                   updated_at = now()
             WHERE run_id = $1 AND page_id = $2
            """,
            run_id,
            result.page_id,
            result.status,
            result.attempt,
            reason,
        )

    async def _schedule_revision(
        self,
        workflow_task: WorkflowTask,
        page: DocumentationTask,
        result: PageEvaluationResult,
    ) -> None:
        next_version = await self.page_repository.reserve_next_version(
            run_id=workflow_task.run_id,
            page_id=page.id,
        )
        write_id = f"write:{page.id}:{next_version}"
        evaluate_id = f"evaluate:{page.id}:{next_version}"
        reviewer_instructions = list(
            workflow_task.input_data.get("reviewer_instructions") or []
        )
        await self.page_repository.enqueue_task(
            run_id=workflow_task.run_id,
            task_id=write_id,
            org_id=workflow_task.org_id,
            task_type="write",
            page_id=page.id,
            artifact_version=next_version,
            input_data={
                "task": page.model_dump(),
                "evaluation": result.model_dump(),
                "reviewer_instructions": reviewer_instructions,
            },
        )
        await self.page_repository.enqueue_task(
            run_id=workflow_task.run_id,
            task_id=evaluate_id,
            org_id=workflow_task.org_id,
            task_type="evaluate",
            page_id=page.id,
            artifact_version=next_version,
            dependencies=[write_id],
            input_data={
                "task": page.model_dump(),
                "evidence": [item.model_dump() for item in page.evidence],
                "attempt": result.attempt + 1,
                "reviewer_instructions": reviewer_instructions,
            },
        )

    async def _schedule_review_if_ready(
        self,
        workflow_task: WorkflowTask,
        page: DocumentationTask,
    ) -> None:
        states = await self.page_repository.get_page_states(run_id=workflow_task.run_id)
        if not states or any(state.status != PageStatus.PASSED.value for state in states):
            return
        review_id = CROSS_PAGE_REVIEW_TASK_ID
        get_tasks = getattr(self.page_repository, "get_tasks", None)
        if get_tasks is not None:
            existing = [
                task
                for task in await get_tasks(run_id=workflow_task.run_id)
                if task.task_type == "cross_page_review"
            ]
            review_id = f"{CROSS_PAGE_REVIEW_TASK_ID}:{len(existing) + 1}"
        await self.page_repository.enqueue_task(
            run_id=workflow_task.run_id,
            task_id=review_id,
            org_id=workflow_task.org_id,
            task_type="cross_page_review",
            dependencies=[workflow_task.task_id],
            input_data={"last_page_task": page.model_dump()},
        )


class CrossPageReviewHandler:
    """Review accepted artifacts and schedule only named page corrections."""

    def __init__(
        self,
        *,
        reviewer_factory: Callable[[], Any],
        drafts_repo: DraftRepository,
        page_repository: PageWorkflowRepository,
    ) -> None:
        self.reviewer_factory = reviewer_factory
        self.drafts_repo = drafts_repo
        self.page_repository = page_repository

    async def __call__(self, workflow_task: WorkflowTask) -> dict[str, Any]:
        states = await self.page_repository.get_page_states(run_id=workflow_task.run_id)
        if not states or any(state.status != PageStatus.PASSED.value for state in states):
            raise ValueError("cross-page review starts only after every page has passed")

        artifacts: dict[str, DocumentArtifact] = {}
        for state in states:
            artifacts[state.page_id] = await _current_artifact(
                self.drafts_repo,
                run_id=workflow_task.run_id,
                page_id=state.page_id,
                expected_version=state.latest_version,
                expected_artifact_id=state.latest_artifact_id,
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
        corrected: list[str] = []
        for correction in corrections:
            page_id = str(getattr(correction, "task_id", "") or "")
            path = str(getattr(correction, "path", "") or "")
            if page_id not in known or path != known[page_id].path:
                raise ValueError("cross-page corrections must name known page IDs")
            if page_id in corrected:
                continue
            corrected.append(page_id)
            await self._schedule_correction(
                workflow_task,
                known[page_id],
                [
                    str(item)
                    for item in getattr(correction, "instructions", []) or []
                    if str(item).strip()
                ],
            )
        return {"passed": False, "corrected_page_ids": corrected}

    async def _schedule_correction(
        self,
        workflow_task: WorkflowTask,
        state: PageState,
        instructions: list[str],
    ) -> None:
        page = await self._load_page_plan(workflow_task.run_id, state)
        next_version = await self.page_repository.reserve_next_version(
            run_id=workflow_task.run_id,
            page_id=state.page_id,
        )
        write_id = f"write:{state.page_id}:{next_version}"
        evaluate_id = f"evaluate:{state.page_id}:{next_version}"
        await _set_page_status(
            self.page_repository,
            run_id=workflow_task.run_id,
            page_id=state.page_id,
            status=PageStatus.REVISING.value,
        )
        await self.page_repository.enqueue_task(
            run_id=workflow_task.run_id,
            task_id=write_id,
            org_id=workflow_task.org_id,
            task_type="write",
            page_id=state.page_id,
            artifact_version=next_version,
            input_data={
                "task": page.model_dump(),
                "reviewer_instructions": instructions,
            },
        )
        await self.page_repository.enqueue_task(
            run_id=workflow_task.run_id,
            task_id=evaluate_id,
            org_id=workflow_task.org_id,
            task_type="evaluate",
            page_id=state.page_id,
            artifact_version=next_version,
            dependencies=[write_id],
            input_data={
                "task": page.model_dump(),
                "evidence": [item.model_dump() for item in page.evidence],
                "attempt": state.evaluation_attempt + 1,
                "reviewer_instructions": instructions,
            },
        )

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
