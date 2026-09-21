"""Page-scoped writer, evaluator, and cross-page review handler tests."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from draftly.agents.schemas import DocumentationTask, ReviewCorrection, ReviewVerdict
from draftly.orchestration.nodes.rubric_grader import RubricGrade
from draftly.orchestration.page_workflow.handlers import (
    CrossPageReviewHandler,
    PageEvaluatorHandler,
    PageWriterHandler,
    compute_page_metrics,
    render_revision_prompt,
)
from draftly.orchestration.page_workflow.models import (
    DocumentArtifact,
    MetricResult,
    PageEvaluationResult,
)
from draftly.orchestration.page_workflow.repository import PageState, WorkflowTask

RUN_ID = "run-1"
ORG_ID = "org-1"


def _task(
    *,
    task_type: str,
    page_id: str = "docs/oauth.md",
    version: int = 1,
    input_data: dict[str, Any] | None = None,
) -> WorkflowTask:
    return WorkflowTask(
        run_id=RUN_ID,
        task_id=f"{task_type}:{page_id}:{version}",
        org_id=ORG_ID,
        task_type=task_type,
        page_id=page_id,
        artifact_version=version,
        dependencies=[],
        status="running",
        infrastructure_retries=0,
        lease_owner="worker-1",
        lease_expires_at=None,
        input_data=dict(input_data or {}),
        output_data=None,
        created_at=None,
        updated_at=None,
    )


def _page_task(page_id: str = "docs/oauth.md") -> DocumentationTask:
    return DocumentationTask(
        id=page_id,
        path=page_id,
        action="update",
        reason="Refresh OAuth guidance",
        requirements=["Document the token refresh failure"],
        evidence=[
            {
                "id": "src/oauth.py",
                "topic": "token refresh",
                "excerpt": "refresh raises TokenExpired",
            }
        ],
    )


def _artifact(
    *,
    page_id: str = "docs/oauth.md",
    version: int = 1,
    content: str | None = None,
) -> DocumentArtifact:
    body = content or ("# OAuth\n\nsrc/oauth token refresh " + "details " * 90)
    return DocumentArtifact(
        page_id=page_id,
        path=page_id,
        artifact_id=f"artifact-{page_id}-{version}",
        version=version,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
        action="update",
        content=body,
    )


def _state(
    page_id: str,
    *,
    status: str = "pending",
    artifact: DocumentArtifact | None = None,
    attempt: int = 0,
) -> PageState:
    return PageState(
        run_id=RUN_ID,
        org_id=ORG_ID,
        page_id=page_id,
        path=page_id,
        action="update",
        status=status,
        latest_artifact_id=artifact.artifact_id if artifact else None,
        latest_version=artifact.version if artifact else 0,
        next_version=(artifact.version + 1) if artifact else 1,
        evaluation_attempt=attempt,
        escalation_reason=None,
        updated_at=None,
    )


class _Drafts:
    def __init__(self, artifacts: list[DocumentArtifact] | None = None) -> None:
        self.artifacts = {artifact.path: artifact for artifact in artifacts or []}

    async def get_path_latest(self, *, run_id: str, path: str) -> Any:
        artifact = self.artifacts.get(path)
        if artifact is None:
            return None
        return SimpleNamespace(
            path=artifact.path,
            action=artifact.action,
            content=artifact.content,
            content_size=len(artifact.content),
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            content_hash=artifact.content_hash,
        )

    async def get_latest(self, *, run_id: str) -> list[Any]:
        return [
            await self.get_path_latest(run_id=run_id, path=path)
            for path in sorted(self.artifacts)
        ]


class _Pages:
    def __init__(
        self,
        states: list[PageState],
        *,
        tasks: list[WorkflowTask] | None = None,
    ) -> None:
        self.states = {state.page_id: state for state in states}
        self.tasks = list(tasks or [])
        self.enqueued: list[dict[str, Any]] = []
        self.evaluations: list[PageEvaluationResult] = []
        self.recorded_artifacts: list[dict[str, Any]] = []

    async def get_page_states(self, *, run_id: str) -> list[PageState]:
        return [self.states[page_id] for page_id in sorted(self.states)]

    async def reserve_next_version(self, *, run_id: str, page_id: str) -> int:
        state = self.states[page_id]
        version = state.next_version
        self.states[page_id] = replace(state, next_version=version + 1)
        return version

    async def record_artifact(self, **kwargs: Any) -> None:
        self.recorded_artifacts.append(kwargs)
        state = self.states[kwargs["page_id"]]
        self.states[kwargs["page_id"]] = replace(
            state,
            latest_artifact_id=kwargs["artifact_id"],
            latest_version=kwargs["version"],
            status="evaluating",
        )

    async def record_evaluation(
        self,
        result: PageEvaluationResult,
        *,
        run_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        self.evaluations.append(result)
        state = self.states[result.page_id]
        page_status = "revising" if result.status == "revision_required" else result.status
        self.states[result.page_id] = replace(
            state,
            status=page_status,
            evaluation_attempt=result.attempt,
            escalation_reason=(
                "; ".join(result.revision_feedback)
                if result.status == "awaiting_human_review"
                else None
            ),
        )

    async def enqueue_task(self, **kwargs: Any) -> None:
        if not any(item["task_id"] == kwargs["task_id"] for item in self.enqueued):
            self.enqueued.append(kwargs)

    async def schedule_revisions(
        self,
        *,
        run_id: str,
        org_id: str,
        revisions: list[Any],
    ) -> list[int]:
        if any(revision.attempt > 3 for revision in revisions):
            raise ValueError("automatic page evaluation attempt must be <= 3")
        versions: list[int] = []
        for revision in revisions:
            state = self.states[revision.page_id]
            version = revision.source_version + 1
            versions.append(version)
            self.states[revision.page_id] = replace(
                state,
                status="revising",
                next_version=max(state.next_version, version + 1),
            )
            await self.enqueue_task(
                run_id=run_id,
                task_id=f"write:{revision.page_id}:{version}",
                org_id=org_id,
                task_type="write",
                page_id=revision.page_id,
                artifact_version=version,
                input_data=revision.write_input,
            )
            await self.enqueue_task(
                run_id=run_id,
                task_id=f"evaluate:{revision.page_id}:{version}",
                org_id=org_id,
                task_type="evaluate",
                page_id=revision.page_id,
                artifact_version=version,
                dependencies=[f"write:{revision.page_id}:{version}"],
                input_data=revision.evaluate_input,
            )
        return versions

    async def get_tasks(self, *, run_id: str) -> list[WorkflowTask]:
        return list(self.tasks)


class _Agent:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.states: list[dict[str, Any] | None] = []

    async def invoke_async(
        self,
        prompt: str,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.prompts.append(prompt)
        self.states.append(invocation_state)
        return SimpleNamespace(structured_output=SimpleNamespace())


class _WriterFactory:
    def __init__(self) -> None:
        self.created_for: list[DocumentationTask] = []
        self.agents: list[_Agent] = []

    def create(self, task: DocumentationTask) -> _Agent:
        self.created_for.append(task)
        agent = _Agent()
        self.agents.append(agent)
        return agent


class _Grader:
    def __init__(self, reasons: list[str] | None = None) -> None:
        self.reasons = reasons or []
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        self.calls.append((draft, evidence))
        return RubricGrade(score=0.2, passed=False, reasons=self.reasons)


class _Reviewer:
    def __init__(self, verdict: Any) -> None:
        self.verdict = verdict
        self.prompts: list[str] = []

    async def invoke_async(
        self,
        prompt: str,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.prompts.append(prompt)
        return SimpleNamespace(structured_output=self.verdict)


async def test_initial_writer_receives_one_page_and_only_its_evidence() -> None:
    page = _page_task()
    artifact = _artifact()
    drafts = _Drafts([artifact])
    pages = _Pages([_state(page.id)])
    factory = _WriterFactory()
    handler = PageWriterHandler(
        writer_factory=factory,
        drafts_repo=drafts,
        page_repository=pages,
    )

    output = await handler(
        _task(
            task_type="write",
            input_data={
                "task": page.model_dump(),
                "unrelated_evidence": [{"id": "docs/unrelated.md"}],
            },
        )
    )

    assert [task.id for task in factory.created_for] == ["docs/oauth.md"]
    prompt = factory.agents[0].prompts[0]
    assert "src/oauth.py" in prompt
    assert "docs/unrelated.md" not in prompt
    assert output["artifact_id"] == artifact.artifact_id
    assert pages.recorded_artifacts == [
        {
            "run_id": RUN_ID,
            "page_id": page.id,
            "artifact_id": artifact.artifact_id,
            "version": 1,
        }
    ]


async def test_writer_replay_reuses_sealed_expected_artifact_without_agent_call() -> None:
    page = _page_task()
    artifact = _artifact()
    class _ReplayDrafts(_Drafts):
        async def prepare_versioned_write(
            self, *, run_id: str, path: str, version: int
        ) -> Any:
            return await self.get_path_latest(run_id=run_id, path=path)

    drafts = _ReplayDrafts([artifact])
    pages = _Pages([_state(page.id, status="evaluating", artifact=artifact)])
    factory = _WriterFactory()
    handler = PageWriterHandler(
        writer_factory=factory,
        drafts_repo=drafts,
        page_repository=pages,
    )

    output = await handler(
        _task(task_type="write", input_data={"task": page.model_dump()})
    )

    assert factory.created_for == []
    assert output["artifact_id"] == artifact.artifact_id


async def test_revision_writer_receives_only_current_page_failure_context() -> None:
    page = _page_task()
    artifact = _artifact(content="# OAuth\n\nOld token refresh behavior.")
    failed = MetricResult(
        name="quality_score",
        score=0.55,
        threshold=0.70,
        passed=False,
        blocking=True,
        reason="Document the token refresh failure",
    )
    evaluation = PageEvaluationResult(
        page_id=page.id,
        artifact_id=artifact.artifact_id,
        version=1,
        content_hash=artifact.content_hash,
        attempt=1,
        status="revision_required",
        score=0.55,
        metrics=[failed],
        revision_feedback=["Document the token refresh failure"],
    )

    prompt = render_revision_prompt(
        page,
        artifact,
        evaluation,
        reviewer_instructions=["Use one term consistently"],
    )
    assert "Current artifact (version 1)" in prompt
    assert artifact.content in prompt
    assert "quality_score: 0.55" in prompt
    assert "Document the token refresh failure" in prompt
    assert "Use one term consistently" in prompt
    assert "docs/unrelated.md" not in prompt

    revised = artifact.model_copy(update={"version": 2, "artifact_id": "artifact-v2"})

    class _RevisionDrafts(_Drafts):
        def __init__(self) -> None:
            super().__init__([artifact])
            self.reads = 0

        async def get_path_latest(self, *, run_id: str, path: str) -> Any:
            self.reads += 1
            self.artifacts[path] = artifact if self.reads == 1 else revised
            return await super().get_path_latest(run_id=run_id, path=path)

    drafts = _RevisionDrafts()
    pages = _Pages([_state(page.id, artifact=artifact, attempt=1)])
    factory = _WriterFactory()
    handler = PageWriterHandler(
        writer_factory=factory,
        drafts_repo=drafts,
        page_repository=pages,
    )
    await handler(
        _task(
            task_type="write",
            version=2,
            input_data={
                "task": page.model_dump(),
                "evaluation": evaluation.model_dump(),
                "reviewer_instructions": ["Use one term consistently"],
            },
        )
    )
    revision_prompt = factory.agents[0].prompts[0]
    assert artifact.content in revision_prompt
    assert "quality_score: 0.55" in revision_prompt
    assert "Use one term consistently" in revision_prompt


def _evaluation_input(page: DocumentationTask, *, attempt: int) -> dict[str, Any]:
    return {
        "task": page.model_dump(),
        "evidence": [item.model_dump() for item in page.evidence],
        "attempt": attempt,
    }


async def test_passing_page_creates_no_revision_task() -> None:
    page = _page_task()
    artifact = _artifact()
    pages = _Pages([_state(page.id, status="evaluating", artifact=artifact)])
    handler = PageEvaluatorHandler(
        rubric_grader=_Grader(),
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    output = await handler(
        _task(task_type="evaluate", input_data=_evaluation_input(page, attempt=1))
    )

    assert output["status"] == "passed"
    assert not [item for item in pages.enqueued if item["task_type"] == "write"]


async def test_newer_artifact_promotion_after_evaluation_cannot_be_overwritten() -> None:
    page = _page_task()
    first = _artifact()
    second = _artifact(version=2)

    class _InterleavingPages(_Pages):
        async def record_evaluation(
            self,
            result: PageEvaluationResult,
            *,
            run_id: str | None = None,
            org_id: str | None = None,
        ) -> None:
            self.evaluations.append(result)
            current = self.states[result.page_id]
            self.states[result.page_id] = replace(
                current,
                latest_artifact_id=second.artifact_id,
                latest_version=second.version,
                status="evaluating",
            )

    pages = _InterleavingPages(
        [_state(page.id, status="evaluating", artifact=first)]
    )
    handler = PageEvaluatorHandler(
        rubric_grader=_Grader(),
        drafts_repo=_Drafts([first]),
        page_repository=pages,
    )

    await handler(
        _task(task_type="evaluate", input_data=_evaluation_input(page, attempt=1))
    )

    state = pages.states[page.id]
    assert state.latest_artifact_id == second.artifact_id
    assert state.status == "evaluating"
    assert pages.enqueued == []


async def test_passing_correction_schedules_review_for_current_artifact_snapshot() -> None:
    page = _page_task()
    artifact = _artifact(version=2)
    prior_review = _task(task_type="cross_page_review", page_id="review")
    pages = _Pages(
        [_state(page.id, status="evaluating", artifact=artifact, attempt=1)],
        tasks=[prior_review],
    )
    handler = PageEvaluatorHandler(
        rubric_grader=_Grader(),
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    output = await handler(
        _task(
            task_type="evaluate",
            version=2,
            input_data=_evaluation_input(page, attempt=2),
        )
    )

    assert output["status"] == "passed"
    reviews = [item for item in pages.enqueued if item["task_type"] == "cross_page_review"]
    assert len(reviews) == 1
    assert reviews[0]["task_id"].startswith("cross-page-review:")
    assert reviews[0]["input_data"]["artifact_snapshot"] == {
        page.id: {
            "artifact_id": artifact.artifact_id,
            "version": artifact.version,
            "content_hash": artifact.content_hash,
        }
    }


async def test_review_scheduling_replay_deduplicates_same_artifact_snapshot() -> None:
    page = _page_task()
    artifact = _artifact()
    pages = _Pages([_state(page.id, status="evaluating", artifact=artifact)])
    handler = PageEvaluatorHandler(
        rubric_grader=_Grader(),
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )
    task = _task(
        task_type="evaluate",
        input_data=_evaluation_input(page, attempt=1),
    )

    await asyncio.gather(handler(task), handler(task))

    reviews = [item for item in pages.enqueued if item["task_type"] == "cross_page_review"]
    assert len(reviews) == 1


async def test_failing_attempt_one_schedules_only_version_two_pair() -> None:
    page = _page_task()
    artifact = _artifact(content="# OAuth\n\nToo short.")
    pages = _Pages([_state(page.id, status="evaluating", artifact=artifact)])
    handler = PageEvaluatorHandler(
        rubric_grader=_Grader(["Document the token refresh failure"]),
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    output = await handler(
        _task(task_type="evaluate", input_data=_evaluation_input(page, attempt=1))
    )

    assert output["status"] == "revision_required"
    assert [item["task_id"] for item in pages.enqueued] == [
        "write:docs/oauth.md:2",
        "evaluate:docs/oauth.md:2",
    ]
    assert pages.enqueued[1]["dependencies"] == ["write:docs/oauth.md:2"]


async def test_failing_attempt_three_escalates_without_automatic_write() -> None:
    page = _page_task()
    artifact = _artifact(version=3, content="# OAuth\n\nStill too short.")
    pages = _Pages(
        [_state(page.id, status="evaluating", artifact=artifact, attempt=2)]
    )
    handler = PageEvaluatorHandler(
        rubric_grader=_Grader(["Still incomplete"]),
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    output = await handler(
        _task(
            task_type="evaluate",
            version=3,
            input_data=_evaluation_input(page, attempt=3),
        )
    )

    assert output["status"] == "awaiting_human_review"
    assert pages.states[page.id].status == "awaiting_human_review"
    assert not [item for item in pages.enqueued if item["task_type"] == "write"]


async def test_missing_evidence_escalates_on_first_attempt_without_revision() -> None:
    page = _page_task()
    artifact = _artifact(content="# OAuth\n\nNo sources available.")
    pages = _Pages([_state(page.id, status="evaluating", artifact=artifact)])
    grader = _Grader()
    handler = PageEvaluatorHandler(
        rubric_grader=grader,
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    output = await handler(
        _task(
            task_type="evaluate",
            input_data={"task": page.model_dump(), "evidence": [], "attempt": 1},
        )
    )

    assert output["status"] == "awaiting_human_review"
    assert not pages.enqueued
    assert grader.calls == []


@pytest.mark.parametrize("evidence", [[{}], [{"excerpt": "no locator"}]])
async def test_signal_free_evidence_escalates_without_grader_or_revision(
    evidence: list[dict[str, Any]],
) -> None:
    page = _page_task()
    artifact = _artifact(content="# OAuth\n\nNo attributable source.")
    pages = _Pages([_state(page.id, status="evaluating", artifact=artifact)])
    grader = _Grader()
    handler = PageEvaluatorHandler(
        rubric_grader=grader,
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    output = await handler(
        _task(
            task_type="evaluate",
            input_data={"task": page.model_dump(), "evidence": evidence, "attempt": 1},
        )
    )

    assert output["status"] == "awaiting_human_review"
    assert grader.calls == []
    assert pages.enqueued == []


async def test_evaluator_rejects_attempt_above_shared_budget_before_side_effects() -> None:
    page = _page_task()
    artifact = _artifact(version=4)
    pages = _Pages([_state(page.id, status="evaluating", artifact=artifact, attempt=3)])
    grader = _Grader()
    handler = PageEvaluatorHandler(
        rubric_grader=grader,
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    with pytest.raises(ValueError, match="attempt.*3"):
        await handler(
            _task(
                task_type="evaluate",
                version=4,
                input_data=_evaluation_input(page, attempt=4),
            )
        )

    assert pages.evaluations == []
    assert pages.enqueued == []
    assert grader.calls == []


async def test_cross_page_review_requires_every_page_to_have_passed() -> None:
    a = _artifact(page_id="docs/a.md")
    b = _artifact(page_id="docs/b.md")
    pages = _Pages(
        [
            _state("docs/a.md", status="passed", artifact=a, attempt=1),
            _state("docs/b.md", status="revision_required", artifact=b, attempt=1),
        ]
    )
    reviewer = _Reviewer(ReviewVerdict(verdict="clean"))
    handler = CrossPageReviewHandler(
        reviewer_factory=lambda: reviewer,
        drafts_repo=_Drafts([a, b]),
        page_repository=pages,
    )

    with pytest.raises(ValueError, match="every page has passed"):
        await handler(_task(task_type="cross_page_review", page_id="review"))
    assert reviewer.prompts == []


@pytest.mark.parametrize("bad_page_id", ["docs/ghost.md", ""])
async def test_cross_page_review_rejects_unknown_or_absent_page_ids(
    bad_page_id: str,
) -> None:
    artifact = _artifact()
    pages = _Pages([_state(artifact.page_id, status="passed", artifact=artifact, attempt=1)])
    verdict = SimpleNamespace(
        verdict="correct",
        corrections=[
            SimpleNamespace(
                task_id=bad_page_id,
                path=bad_page_id,
                instructions=["Use one term consistently"],
            )
        ],
    )
    handler = CrossPageReviewHandler(
        reviewer_factory=lambda: _Reviewer(verdict),
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    with pytest.raises(ValueError, match="known page IDs"):
        await handler(_task(task_type="cross_page_review", page_id="review"))


async def test_cross_page_review_schedules_only_implicated_pages() -> None:
    a = _artifact(page_id="docs/a.md")
    b = _artifact(page_id="docs/b.md")
    pages = _Pages(
        [
            _state("docs/a.md", status="passed", artifact=a, attempt=1),
            _state("docs/b.md", status="passed", artifact=b, attempt=1),
        ]
    )
    reviewer = _Reviewer(
        ReviewVerdict(
            verdict="correct",
            corrections=[
                ReviewCorrection(
                    task_id="docs/b.md",
                    path="docs/b.md",
                    instructions=["Use one term consistently"],
                )
            ],
        )
    )
    handler = CrossPageReviewHandler(
        reviewer_factory=lambda: reviewer,
        drafts_repo=_Drafts([a, b]),
        page_repository=pages,
    )

    output = await handler(_task(task_type="cross_page_review", page_id="review"))

    assert output == {"passed": False, "corrected_page_ids": ["docs/b.md"]}
    assert [item["task_id"] for item in pages.enqueued] == [
        "write:docs/b.md:2",
        "evaluate:docs/b.md:2",
    ]
    assert pages.enqueued[0]["input_data"]["reviewer_instructions"] == [
        "Use one term consistently"
    ]


async def test_cross_page_review_replay_reuses_correction_pair() -> None:
    artifact = _artifact()
    pages = _Pages(
        [_state(artifact.page_id, status="passed", artifact=artifact, attempt=1)]
    )
    reviewer = _Reviewer(
        ReviewVerdict(
            verdict="correct",
            corrections=[
                ReviewCorrection(
                    task_id=artifact.page_id,
                    path=artifact.path,
                    instructions=["Use one term consistently"],
                )
            ],
        )
    )
    handler = CrossPageReviewHandler(
        reviewer_factory=lambda: reviewer,
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )
    review_task = _task(
        task_type="cross_page_review",
        page_id="review",
        input_data={
            "artifact_snapshot": {
                artifact.page_id: {
                    "artifact_id": artifact.artifact_id,
                    "version": artifact.version,
                    "content_hash": artifact.content_hash,
                }
            }
        },
    )

    first = await handler(review_task)
    second = await handler(review_task)

    assert first == second
    assert [item["task_id"] for item in pages.enqueued] == [
        "write:docs/oauth.md:2",
        "evaluate:docs/oauth.md:2",
    ]


async def test_cross_page_review_validates_all_corrections_before_mutating() -> None:
    a = _artifact(page_id="docs/a.md")
    b = _artifact(page_id="docs/b.md")
    pages = _Pages(
        [
            _state("docs/a.md", status="passed", artifact=a, attempt=1),
            _state("docs/b.md", status="passed", artifact=b, attempt=1),
        ]
    )
    verdict = SimpleNamespace(
        verdict="correct",
        corrections=[
            SimpleNamespace(
                task_id="docs/a.md", path="docs/a.md", instructions=["valid"]
            ),
            SimpleNamespace(
                task_id="docs/ghost.md",
                path="docs/ghost.md",
                instructions=["invalid"],
            ),
        ],
    )
    handler = CrossPageReviewHandler(
        reviewer_factory=lambda: _Reviewer(verdict),
        drafts_repo=_Drafts([a, b]),
        page_repository=pages,
    )

    with pytest.raises(ValueError, match="known page IDs"):
        await handler(_task(task_type="cross_page_review", page_id="review"))

    assert pages.enqueued == []
    assert pages.states["docs/a.md"].status == "passed"


async def test_cross_page_correction_cannot_create_attempt_four() -> None:
    artifact = _artifact()
    pages = _Pages(
        [_state(artifact.page_id, status="passed", artifact=artifact, attempt=3)]
    )
    reviewer = _Reviewer(
        ReviewVerdict(
            verdict="correct",
            corrections=[
                ReviewCorrection(
                    task_id=artifact.page_id,
                    path=artifact.path,
                    instructions=["Revise terminology"],
                )
            ],
        )
    )
    handler = CrossPageReviewHandler(
        reviewer_factory=lambda: reviewer,
        drafts_repo=_Drafts([artifact]),
        page_repository=pages,
    )

    with pytest.raises(ValueError, match="attempt.*3"):
        await handler(_task(task_type="cross_page_review", page_id="review"))

    assert pages.enqueued == []


@pytest.mark.parametrize(
    "bad_page_id",
    ["../docs/a.md", "/docs/a.md", "docs//a.md", "./docs/a.md", "docs\\a.md"],
)
async def test_page_handlers_reject_noncanonical_page_ids(bad_page_id: str) -> None:
    page = _page_task().model_copy(update={"id": bad_page_id, "path": bad_page_id})
    handler = PageWriterHandler(
        writer_factory=_WriterFactory(),
        drafts_repo=_Drafts(),
        page_repository=_Pages([]),
    )

    with pytest.raises(ValueError, match="canonical"):
        await handler(
            _task(
                task_type="write",
                page_id=bad_page_id,
                input_data={"task": page.model_dump()},
            )
        )


def test_compute_page_metrics_exposes_weighted_components_and_gate() -> None:
    content = "src/oauth token refresh " + "detail " * 90
    metrics = compute_page_metrics(
        [{"id": "src/oauth.py", "topic": "token refresh"}],
        content,
    )

    assert [metric.name for metric in metrics] == [
        "citation_coverage",
        "topic_completeness",
        "detail",
        "quality_score",
    ]
    assert [metric.blocking for metric in metrics] == [False, False, False, True]
    assert metrics[-1].score == pytest.approx(
        metrics[0].score * 0.4 + metrics[1].score * 0.3 + metrics[2].score * 0.3
    )
    assert metrics[-1].threshold == 0.70
    assert metrics[-1].passed is True
