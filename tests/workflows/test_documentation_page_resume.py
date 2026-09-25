"""Workflow-level documentation resume tests (plan task 6 §step 5).

Drives ``DocumentationWorkflowNode.resume`` end-to-end over the real
``PageWorkflowRepository`` + handlers, then re-checks payloads against the
delivery-gate conditions:

* ``approve`` accepts the escalated artifacts so the workflow can continue
  to the changelog (the hardened ``documentation_passed`` edge fires);
* ``request_changes`` requires a comment, schedules exactly one human-guided
  write/evaluate pair per escalated page, and pauses again when the revision
  still fails;
* ``reject`` cancels pending work and fails the pages so delivery never runs;
* a missing sealed-artifact claim can never satisfy the changelog gate, even
  when a result claims ``passed``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from strands.multiagent.base import MultiAgentResult, Status
from strands.multiagent.graph import GraphState, NodeResult

from draftly.agents.documentation.writer import WriterFactory
from draftly.events.stream_envelope import documentation_progress_envelope
from draftly.orchestration.nodes.base import agent_result, node_data
from draftly.orchestration.nodes.rubric_grader import build_docs_rubric_grader
from draftly.orchestration.page_workflow.handlers import (
    CrossPageReviewHandler,
    PageEvaluatorHandler,
    PageWriterHandler,
)
from draftly.orchestration.page_workflow.node import DocumentationWorkflowNode
from draftly.orchestration.page_workflow.repository import PageWorkflowRepository
from draftly.orchestration.routing.conditions import (
    documentation_delivery_ready,
    documentation_passed,
)
from draftly.persistence.repositories.drafts import DraftRepository
from tests.unit.orchestration.page_workflow.test_node import IMPACT_NO_EVIDENCE
from tests.unit.orchestration.page_workflow.test_repository import FakeClient

ORIGINAL_TASK = (
    '{"event_id": "e-1", "event_type": "pull_request.opened", '
    '"repository": "acme/api", "pull_request": {"head": {"sha": "abc123"}}}'
)


def _node_task(impact: dict) -> list[dict]:
    sections = [
        "Original Task: " + ORIGINAL_TASK,
        "Inputs from previous nodes:",
        "From impact:",
        "  - Agent: " + json.dumps(impact),
    ]
    return [{"text": "\n".join(sections)}]


def _state_with_document(payload: dict) -> GraphState:
    state = GraphState()
    state.results["document"] = NodeResult(
        result=agent_result(payload),
        status=Status.COMPLETED,
    )
    return state


def _payload(result: dict, files: list[dict], sealed_pages: list[dict]) -> dict:
    payload: dict[str, object] = {"result": result, "files": files}
    if sealed_pages:
        payload["sealed_pages"] = sealed_pages
    return payload


def _wired_node(*, fail_all_writes: bool = False):
    """Build the node over one FakeClient-backed workflow + draft store."""
    database = FakeClient()
    pages = PageWorkflowRepository(database=database)
    drafts = DraftRepository(database=database)
    writer_recorder: list[tuple[str, int]] = []

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

                class _Verdict:
                    verdict = "clean"
                    corrections = []

                return SimpleNamespace(structured_output=_Verdict())

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
    return node, pages, writer_recorder


def _invoke(node: DocumentationWorkflowNode, impact: dict, run_id: str) -> MultiAgentResult:
    return node.invoke_async(
        _node_task(impact),
        invocation_state={"run_id": run_id, "project_id": "org-1"},
    )


def _node_result(result: MultiAgentResult) -> dict:
    return node_data(result, "document")


# -- approve ----------------------------------------------------------------


async def test_approve_accepts_artifacts_and_can_continue_to_changelog() -> None:
    node, _, _ = _wired_node()
    escalated = await _invoke(node, IMPACT_NO_EVIDENCE, "run-a")
    report_data = _node_result(escalated)
    assert report_data["result"]["escalated_page_ids"] == ["docs/widgets.md"]

    resumed = await node.resume("run-a", "approve", "accepted")
    assert resumed.passed is True
    assert resumed.escalated_page_ids == []

    # Continued through the document gate: a re-run against the accepted
    # state reports a fully-passed, sealed result. Replaying the same payload
    # through the hardened gate permits the changelog edge.
    again = await _invoke(node, IMPACT_NO_EVIDENCE, "run-a")
    payload = _node_result(again)
    assert payload["result"]["passed"] is True
    state = _state_with_document(payload)
    # The changelog gate fires (hardened passed result carrying sealed pages);
    # delivery additionally needs the changelog evaluator, which the plan
    # exercises through its own edge — so only the changelog edge is asserted.
    assert documentation_passed(state) is True


# -- request_changes --------------------------------------------------------


async def test_request_changes_requires_a_comment() -> None:
    node, _, _ = _wired_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-b")
    with pytest.raises(ValueError, match="non-empty comment"):
        await node.resume("run-b", "request_changes", "  ")
    with pytest.raises(ValueError, match="non-empty comment"):
        await node.resume("run-b", "request_changes", None)


async def test_request_changes_schedules_one_human_guided_revision_per_page() -> None:
    node, pages, _ = _wired_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-c")
    before = await pages.get_page_states(run_id="run-c")
    assert before[0].status == "awaiting_human_review"

    report = await node.resume("run-c", "request_changes", "please add a rationale")

    # The review restarted the run for the revision: the original page pauses
    # again (no evidence in this run), and exactly one human-guided version
    # (v2) write/evaluate pair exists per escalated page.
    assert report.passed is False
    assert report.escalated_page_ids == ["docs/widgets.md"]

    states = await pages.get_page_states(run_id="run-c")
    assert states[0].status == "awaiting_human_review"
    assert states[0].latest_version >= 2

    tasks = await pages.get_tasks(run_id="run-c")
    v2 = [t for t in tasks if t.artifact_version == 2]
    write_v2 = [t for t in v2 if t.task_type == "write"]
    evaluate_v2 = [t for t in v2 if t.task_type == "evaluate"]
    assert {t.task_type for t in v2} == {"write", "evaluate"}
    assert all(t.input_data.get("human_guided") is True for t in v2)
    assert all(t.input_data.get("revision_comment") == "please add a rationale" for t in v2)
    # The human-guided evaluate task records the page's existing attempt, so
    # a fourth automatic attempt is never booked; the write carries no attempt.
    assert all("attempt" in t.input_data for t in evaluate_v2)
    assert all("attempt" not in t.input_data for t in write_v2)
    assert all(t.input_data["attempt"] == before[0].evaluation_attempt for t in evaluate_v2)


async def test_duplicate_request_changes_is_a_noop() -> None:
    node, pages, _ = _wired_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-d")
    first = await node.resume("run-d", "request_changes", "add a rationale")
    states = await pages.get_page_states(run_id="run-d")
    assert states[0].status == "awaiting_human_review"

    second = await node.resume("run-d", "request_changes", "add a rationale")
    assert second.model_dump() == first.model_dump()


# -- reject -----------------------------------------------------------------


async def test_reject_cancels_pending_work_and_blocks_delivery() -> None:
    node, pages, _ = _wired_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-e")
    # Add a queued task: rejection must cancel it so it can never execute.
    await pages.enqueue_task(
        run_id="run-e",
        task_id="write:pending-page:1",
        org_id="org-1",
        task_type="write",
        page_id="docs/extra.md",
        artifact_version=1,
    )

    report = await node.resume("run-e", "reject", "out of scope")

    assert report.passed is False
    assert report.failed_page_ids == ["docs/widgets.md"]
    states = await pages.get_page_states(run_id="run-e")
    assert any(s.status == "failed" for s in states)

    tasks = await pages.get_tasks(run_id="run-e")
    pending = next(t for t in tasks if t.task_id == "write:pending-page:1")
    assert pending.status == "cancelled"

    # Delivery must never run from a rejected result.
    payload = _payload(
        report.model_dump(),
        files=[{"path": "docs/widgets.md", "action": "update"}],
        sealed_pages=[],
    )
    assert documentation_passed(_state_with_document(payload)) is False
    assert documentation_delivery_ready(_state_with_document(payload)) is False


# -- changelog safety -------------------------------------------------------


async def test_missing_sealed_artifact_blocks_changelog_gate() -> None:
    node_repo = PageWorkflowRepository(database=FakeClient())
    _ = node_repo
    payload = _payload(
        {"passed": True, "escalated_page_ids": [], "failed_page_ids": []},
        files=[{"path": "docs/widgets.md", "action": "update"}],
        sealed_pages=[],  # contradicts the passed claim
    )
    assert documentation_passed(_state_with_document(payload)) is False


async def test_passed_with_escalation_cannot_schedule_changelog() -> None:
    payload = _payload(
        {"passed": True, "escalated_page_ids": ["docs/widgets.md"], "failed_page_ids": []},
        files=[{"path": "docs/widgets.md", "action": "update"}],
        sealed_pages=[{"path": "docs/widgets.md"}],
    )
    assert documentation_passed(_state_with_document(payload)) is False
    assert documentation_delivery_ready(_state_with_document(payload)) is False


# -- events ---------------------------------------------------------------


async def test_resume_emits_workflow_resumed_event() -> None:
    node, _, _ = _wired_node()
    events: list[dict] = []

    class _Sink:
        async def __call__(self, progress: dict) -> None:
            events.append(progress)

    node.progress_sink = _Sink()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-f")
    await node.resume("run-f", "approve", "ok")

    kinds = {e["event_type"] for e in events}
    assert "documentation.workflow.resumed" in kinds
    assert "documentation.page.evaluated" in kinds
    assert all(
        documentation_progress_envelope(
            e["event_type"],
            run_id=e["run_id"],
            task_id=e.get("task_id") or "",
            page_id=e.get("page_id") or "",
            artifact_version=e.get("artifact_version"),
            attempt=e.get("attempt"),
            status=e.get("status") or "",
        ).type
        == "documentation.progress"
        for e in events
    )
