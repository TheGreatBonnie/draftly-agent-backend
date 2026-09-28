"""DocumentationWorkflowNode orchestration tests.

Exercises the node's invoke contract directly (no Strands graph): idempotent
seeding and re-entry, deterministic execution through the real
``PageWorkflowRepository`` + handlers + FakeClient, aggregation into
``DocumentationWorkflowResult``, and the vacuous / offline failure modes a
graph run cannot reach.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from strands.multiagent.base import Status

from draftly.agents.documentation.writer import WriterFactory
from draftly.agents.schemas import DocumentationTask, ImpactAnalysis, ReviewVerdict
from draftly.orchestration.nodes.base import node_data
from draftly.orchestration.nodes.rubric_grader import build_docs_rubric_grader
from draftly.orchestration.page_workflow.handlers import (
    CrossPageReviewHandler,
    PageEvaluatorHandler,
    PageWriterHandler,
)
from draftly.orchestration.page_workflow.node import DocumentationWorkflowNode
from draftly.orchestration.page_workflow.repository import PageWorkflowRepository
from draftly.persistence.repositories.drafts import DraftRepository
from tests.unit.orchestration.page_workflow.test_repository import FakeClient

ORIGINAL_TASK = (
    '{"event_id": "e-1", "event_type": "pull_request.opened", '
    '"repository": "acme/api", "pull_request": {"head": {"sha": "abc123"}}}'
)

IMPACT_WITH_EVIDENCE = {
    "action": "update",
    "affected_documents": ["docs/widgets.md"],
    "rationale": "behavior changed",
    "tasks": [
        {
            "id": "docs/widgets.md",
            "path": "docs/widgets.md",
            "action": "update",
            "evidence": [{"id": "docs/widgets.md", "topic": "widgets"}],
        }
    ],
}

IMPACT_NO_EVIDENCE = {
    "action": "update",
    "affected_documents": ["docs/widgets.md"],
    "rationale": "behavior changed",
    "tasks": [
        {
            "id": "docs/widgets.md",
            "path": "docs/widgets.md",
            "action": "update",
        }
    ],
}

IMPACT_SLUG_ID = {
    "action": "update",
    "affected_documents": ["docs/how-to/oauth-authorization-url.md"],
    "rationale": "behavior changed",
    "tasks": [
        {
            # The LLM planner may emit a slug id ("task1") alongside the real
            # repo path. The page workflow must canonicalize id -> path at
            # seed time so pages, drafts, and review share one key.
            "id": "task1",
            "path": "docs/how-to/oauth-authorization-url.md",
            "action": "update",
            "evidence": [
                {
                    "id": "src/oauth.py",
                    "topic": "authorization-code exchange",
                }
            ],
        }
    ],
}

IMPACT_EMPTY = {
    "action": "update",
    "affected_documents": [],
    "rationale": "no pages planned",
    "tasks": [],
}


def _node_task(impact: dict, evidence: list | None) -> list[dict]:
    sections = [
        "Original Task: " + ORIGINAL_TASK,
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: " + json.dumps(impact),
    ]
    if evidence is not None:
        sections.append("From research:")
        sections.append("  - Agent: " + json.dumps({"items": evidence}))
    return [{"text": "\n".join(sections)}]


def _wired_node(*, fail_all_writes: bool = False):
    """Build the node over one FakeClient-backed workflow + draft store."""
    database = FakeClient()
    pages = PageWorkflowRepository(database=database)
    drafts = DraftRepository(database=database)
    writer_recorder: list[tuple[str, int]] = []
    review_recorder: list[str] = []

    def sealing_builder(model, tools, runtime=None, agent_id=None, node_id=None):
        del model, tools, runtime, agent_id, node_id

        class _SealingWriter:
            async def invoke_async(self, prompt, invocation_state=None, **kwargs):
                del prompt, kwargs
                state = invocation_state or {}
                page_id = state["page_id"]
                version = int(state["artifact_version"])
                if fail_all_writes:
                    raise RuntimeError(f"seal failure for {page_id} v{version}")
                writer_recorder.append((page_id, version))
                content = f"widgets is implemented and documented as {page_id} " * 30
                revision = await drafts.create_revision(
                    run_id=state["run_id"],
                    org_id=state.get("project_id"),
                    generation=version,
                    path=page_id,
                    action="update",
                    version=version,
                )
                await drafts.append_chunk(revision.id, content)
                await drafts.finalize(revision.id)

        return _SealingWriter()

    def reviewer_builder(model, tools, runtime=None, agent_id=None, node_id=None):
        del model, tools, runtime, agent_id, node_id

        class _CleanReviewer:
            async def invoke_async(self, prompt, invocation_state=None, **kwargs):
                del invocation_state, kwargs
                review_recorder.append(prompt)
                return SimpleNamespace(
                    structured_output=ReviewVerdict(verdict="clean", corrections=[])
                )

        return _CleanReviewer()

    node = DocumentationWorkflowNode(
        "document",
        repository=pages,
        handlers={
            "write": PageWriterHandler(
                writer_factory=WriterFactory(model=None, tools=[], builder=sealing_builder),
                drafts_repo=drafts,
                page_repository=pages,
            ),
            "evaluate": PageEvaluatorHandler(
                rubric_grader=build_docs_rubric_grader(None, rubric="docs rubric"),
                drafts_repo=drafts,
                page_repository=pages,
            ),
            "cross_page_review": CrossPageReviewHandler(
                reviewer_factory=lambda: reviewer_builder(None, []),
                drafts_repo=drafts,
                page_repository=pages,
            ),
        },
    )
    return node, pages, writer_recorder, review_recorder


def _invoke(node: DocumentationWorkflowNode, impact: dict, evidence: list | None, run_id: str):
    return node.invoke_async(
        _node_task(impact, evidence),
        invocation_state={"run_id": run_id, "project_id": "org-1"},
    )


async def test_fresh_run_seeds_tasks_and_reports_passed_pages() -> None:
    node, pages, writer_recorder, review_recorder = _wired_node()
    result = await _invoke(
        node,
        IMPACT_WITH_EVIDENCE,
        [{"id": "docs/widgets.md", "topic": "widgets"}],
        "run-a",
    )

    assert result.status == Status.COMPLETED
    report = node_data(result, "document")["result"]
    assert report["passed"] is True
    assert report["ready_for_review"] is True
    assert report["page_count"] == 1
    assert report["passed_page_ids"] == ["docs/widgets.md"]
    assert report["failed_page_ids"] == []
    assert report["escalated_page_ids"] == []
    # one write, one evaluation, one cross-page review
    assert writer_recorder == [("docs/widgets.md", 1)]
    assert len(review_recorder) == 1
    tasks = await pages.get_tasks(run_id="run-a")
    task_ids = {t.task_id for t in tasks}
    assert {
        "write:docs/widgets.md:1",
        "evaluate:docs/widgets.md:1",
    } <= task_ids
    assert any(t.startswith("cross-page-review:") for t in task_ids)
    assert len(task_ids) == 3


async def test_slug_plan_id_is_canonicalized_to_path_at_seed() -> None:
    """LLM plan ids (task1) must never leak into page / draft / review keying.

    The planner may emit an id that is a slug ("task1") while ``path`` names
    the real repository file. The page workflow keys pages, writer invocation
    state, draft scope, and the cross-page review invariant by page id; when
    the id differs from the path, the draft tools' ``start_draft`` guard
    rejects the writer's own path ("outside assigned page") and the review
    rejects the state ("page path must match canonical page ID"). Seed-time
    canonicalization (``task.id = task.path``) puts every layer on one key.
    """
    node, pages, writer_recorder, review_recorder = _wired_node()
    result = await _invoke(
        node,
        IMPACT_SLUG_ID,
        [{"id": "src/oauth.py", "topic": "authorization-code exchange"}],
        "run-slug",
    )

    assert result.status == Status.COMPLETED
    report = node_data(result, "document")["result"]
    assert report["passed"] is True
    assert report["passed_page_ids"] == ["docs/how-to/oauth-authorization-url.md"]
    assert writer_recorder == [("docs/how-to/oauth-authorization-url.md", 1)]
    assert len(review_recorder) == 1
    tasks = await pages.get_tasks(run_id="run-slug")
    task_ids = {t.task_id for t in tasks}
    assert "write:docs/how-to/oauth-authorization-url.md:1" in task_ids
    assert "evaluate:docs/how-to/oauth-authorization-url.md:1" in task_ids


async def test_seed_threads_repository_into_write_tasks() -> None:
    """The event repository name must reach the write task's input_data.

    The writer handler hydrates the current repo file body from the documents
    store on first writes; it needs the repository from the original PR event
    threaded through the seed so update prompts carry real current content.
    """
    node, pages, writer_recorder, review_recorder = _wired_node()
    sections = [
        'Original Task: {"event_id": "e-1", "event_type": "pull_request.opened", '
        '"repository": "TheGreatBonnie/authly", '
        '"pull_request": {"head": {"sha": "abc123"}}}',
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: " + json.dumps(IMPACT_WITH_EVIDENCE),
        "From research:",
        "  - Agent: " + json.dumps({"items": [{"id": "docs/widgets.md", "topic": "widgets"}]}),
    ]
    result = await node.invoke_async(
        [{"text": "\n".join(sections)}],
        invocation_state={"run_id": "run-repo", "project_id": "org-1"},
    )

    assert result.status == Status.COMPLETED
    tasks = await pages.get_tasks(run_id="run-repo")
    write = next(task for task in tasks if task.task_type == "write")
    assert write.input_data.get("repository") == "TheGreatBonnie/authly"


@pytest.mark.parametrize(
    "repository,head_sha",
    [("", "abc123"), ("acme/api", "")],
)
async def test_pr_writer_rejects_incomplete_source_assignment(
    repository: str, head_sha: str
) -> None:
    node, pages, writer_recorder, _ = _wired_node()
    event = {
        "event_id": "e-1",
        "event_type": "pull_request.opened",
        "repository": repository,
        "pull_request": {"head": {"sha": head_sha}},
    }
    sections = [
        "Original Task: " + json.dumps(event),
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: " + json.dumps(IMPACT_WITH_EVIDENCE),
        "From research:",
        "  - Agent: " + json.dumps({"items": [{"id": "docs/widgets.md", "topic": "widgets"}]}),
    ]

    result = await node.invoke_async(
        [{"text": "\n".join(sections)}],
        invocation_state={"run_id": "run-invalid", "project_id": "org-1"},
    )

    assert result.status == Status.FAILED
    assert await pages.get_tasks(run_id="run-invalid") == []
    assert writer_recorder == []


async def test_seed_pins_source_pr_repository_and_sha_in_page_assignment() -> None:
    node, pages, _, _ = _wired_node()
    sections = [
        'Original Task: {"event_id": "e-1", "event_type": "pull_request.opened", '
        '"repository": "TheGreatBonnie/authly", '
        '"pull_request": {"head": {"sha": "abc123"}}}',
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: " + json.dumps(IMPACT_WITH_EVIDENCE),
        "From research:",
        "  - Agent: " + json.dumps({"items": [{"id": "docs/widgets.md", "topic": "widgets"}]}),
    ]
    result = await node.invoke_async(
        [{"text": "\n".join(sections)}],
        invocation_state={"run_id": "run-pinned", "project_id": "org-1"},
    )

    assert result.status == Status.COMPLETED
    tasks = await pages.get_tasks(run_id="run-pinned")
    for workflow_task in tasks:
        if workflow_task.task_type not in {"write", "evaluate"}:
            continue
        assignment = workflow_task.input_data["task"]
        assert assignment["repository"] == "TheGreatBonnie/authly"
        assert assignment["head_sha"] == "abc123"


async def test_reentry_is_idempotent_and_reuses_sealed_artifacts() -> None:
    node, pages, writer_recorder, review_recorder = _wired_node()
    first = await _invoke(
        node,
        IMPACT_WITH_EVIDENCE,
        [{"id": "docs/widgets.md", "topic": "widgets"}],
        "run-b",
    )
    assert first.status == Status.COMPLETED

    second = await _invoke(
        node,
        IMPACT_WITH_EVIDENCE,
        [{"id": "docs/widgets.md", "topic": "widgets"}],
        "run-b",
    )
    assert second.status == Status.COMPLETED

    # no duplicate page states, tasks, or writes
    assert len(await pages.get_page_states(run_id="run-b")) == 1
    assert len(await pages.get_tasks(run_id="run-b")) == 3
    assert writer_recorder == [("docs/widgets.md", 1)]
    assert len(review_recorder) == 1


async def test_empty_plan_completes_as_vacuous_pass() -> None:
    node, pages, _, _ = _wired_node()
    result = await _invoke(node, IMPACT_EMPTY, None, "run-c")

    assert result.status == Status.COMPLETED
    report = node_data(result, "document")["result"]
    assert report["passed"] is True
    assert report["page_count"] == 0
    assert report["passed_page_ids"] == []
    # nothing was seeded
    assert await pages.get_page_states(run_id="run-c") == []
    assert await pages.get_tasks(run_id="run-c") == []


async def test_infrastructure_write_failure_fails_the_node() -> None:
    node, _, _, _ = _wired_node(fail_all_writes=True)
    result = await _invoke(
        node,
        IMPACT_WITH_EVIDENCE,
        [{"id": "docs/widgets.md", "topic": "widgets"}],
        "run-d",
    )

    assert result.status == Status.FAILED
    payload = node_data(result, "document")
    assert payload["result"]["passed"] is False
    assert "workflow" in payload["error"]


async def test_no_evidence_escalates_to_human_review() -> None:
    node, _, _, _ = _wired_node()
    result = await _invoke(node, IMPACT_NO_EVIDENCE, [], "run-e")

    assert result.status == Status.COMPLETED
    report = node_data(result, "document")["result"]
    assert report["passed"] is False
    assert report["ready_for_review"] is True
    assert report["escalated_page_ids"] == ["docs/widgets.md"]
    assert report["failed_page_ids"] == []


async def test_context_evidence_evaluates_instead_of_escalating() -> None:
    """A page with context evidence must reach the quality gate, not escalate.

    IMPACT_NO_EVIDENCE carries a task with no evidence, which is the exact
    production shape: the impact agent emits tasks and omits evidence. Before
    the fix the node read only deps["research"], so this page escalated.
    """
    node, _pages, _writer, _review = _wired_node()
    sections = [
        "Original Task: " + ORIGINAL_TASK,
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: " + json.dumps(IMPACT_NO_EVIDENCE),
        "From context:",
        "  - Agent: "
        + json.dumps({"items": [{"id": "docs/widgets.md", "topic": "widgets"}]}),
    ]
    result = await node.invoke_async(
        [{"text": "\n".join(sections)}],
        invocation_state={"run_id": "run-ctx", "project_id": "org-1"},
    )

    assert result.status == Status.COMPLETED
    report = node_data(result, "document")["result"]
    assert report["escalated_page_ids"] == []
    assert report["passed"] is True
    assert report["passed_page_ids"] == ["docs/widgets.md"]


async def test_empty_evidence_is_logged(monkeypatch) -> None:
    """A page the resolver could not source must say so out loud."""
    import draftly.agents.documentation.planning as planning

    events: list[dict] = []

    class _Capture:
        def warning(self, event, **kwargs):
            events.append({"event": event, **kwargs})

    monkeypatch.setattr(planning, "logger", _Capture())

    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=[DocumentationTask(id="docs/a.md", path="docs/a.md")],
    )
    planning.resolve_task_evidence(impact, {})

    assert [e for e in events if e["event"] == "page_evidence_empty"]
    assert events[0]["paths"] == ["docs/a.md"]
    assert events[0]["page_count"] == 1


async def test_missing_run_id_fails() -> None:
    node, _, _, _ = _wired_node()
    result = await node.invoke_async(
        _node_task(IMPACT_WITH_EVIDENCE, None),
        invocation_state={"project_id": "org-1"},
    )
    assert result.status == Status.FAILED
    assert "run_id" in node_data(result, "document")["error"]


async def test_unwired_repository_fails() -> None:
    node = DocumentationWorkflowNode("document", repository=None, handlers=None)
    result = await node.invoke_async(
        _node_task(IMPACT_WITH_EVIDENCE, None),
        invocation_state={"run_id": "run-f", "project_id": "org-1"},
    )
    assert result.status == Status.FAILED
    assert "not wired" in node_data(result, "document")["error"]


async def test_invalid_impact_fails() -> None:
    node, _, _, _ = _wired_node()
    result = await node.invoke_async(
        _node_task({"action": "update", "tasks": "not-a-list"}, None),
        invocation_state={"run_id": "run-g", "project_id": "org-1"},
    )
    assert result.status == Status.FAILED
    assert "impact" in node_data(result, "document")["error"]


# -- resume ----------------------------------------------------------------


async def test_resume_approve_marks_escalated_pages_passed() -> None:
    node, _, _, _ = _wired_node()
    escalated = await _invoke(node, IMPACT_NO_EVIDENCE, [], "run-h")
    assert node_data(escalated, "document")["result"]["escalated_page_ids"] == ["docs/widgets.md"]

    report = await node.resume("run-h", "approve", "reviewed, looks good")

    assert report.passed is True
    assert report.ready_for_review is True
    assert report.page_count == 1
    assert report.escalated_page_ids == []
    assert report.passed_page_ids == ["docs/widgets.md"]
    states = await node.repository.get_page_states(run_id="run-h")
    assert states[0].status == "passed"

    # Duplicate resume with the same decision is a no-op.
    again = await node.resume("run-h", "approve", "reviewed, looks good")
    assert again.model_dump() == report.model_dump()


async def test_resume_approve_is_idempotent() -> None:
    node, _, _, _ = _wired_node()
    await _invoke(
        node,
        IMPACT_WITH_EVIDENCE,
        [{"id": "docs/widgets.md", "topic": "widgets"}],
        "run-i",
    )
    first = await node.resume("run-i", "approve", "unnecessary")
    second = await node.resume("run-i", "approve", "unnecessary")
    assert first.passed is True
    assert first.model_dump() == second.model_dump()


async def test_resume_reports_pages_not_covered_by_approve() -> None:
    node, pages, _, _ = _wired_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, [], "run-j")
    # A page in a non-escalated terminal state is untouched by approval and
    # stays visible in the aggregate report.
    pages.database.page_states[0]["status"] = "failed"
    report = await node.resume("run-j", "approve", "partial")
    assert report.passed is False
    assert report.ready_for_review is False
    assert report.failed_page_ids == ["docs/widgets.md"]


async def test_resume_unknown_decision_raises() -> None:
    node, _, _, _ = _wired_node()
    with pytest.raises(ValueError, match="decision"):
        await node.resume("run-k", "merge", "nope")


async def test_resume_request_changes_requires_a_comment() -> None:
    node, _, _, _ = _wired_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, [], "run-l")
    with pytest.raises(ValueError, match="non-empty comment"):
        await node.resume("run-l", "request_changes", "   ")
    with pytest.raises(ValueError, match="non-empty comment"):
        await node.resume("run-l", "request_changes", None)


async def test_resume_reject_fails_escalated_pages() -> None:
    node, _, _, _ = _wired_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, [], "run-n")
    report = await node.resume("run-n", "reject", "out of scope")
    assert report.passed is False
    assert report.ready_for_review is False
    assert report.failed_page_ids == ["docs/widgets.md"]
    states = await node.repository.get_page_states(run_id="run-n")
    assert states[0].status == "failed"
    assert "rejected by human review" in states[0].escalation_reason


async def test_resume_requires_wired_repository() -> None:
    node = DocumentationWorkflowNode("document", repository=None, handlers=None)
    with pytest.raises(RuntimeError, match="not wired"):
        await node.resume("run-m", "approve", "nope")
