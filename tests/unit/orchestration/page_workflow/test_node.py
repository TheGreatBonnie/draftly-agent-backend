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
from draftly.agents.schemas import ReviewVerdict
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

ORIGINAL_TASK = '{"event_id": "e-1", "event_type": "pull_request.opened"}'

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
    assert node_data(escalated, "document")["result"]["escalated_page_ids"] == [
        "docs/widgets.md"
    ]

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
