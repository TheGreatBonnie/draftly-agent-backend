"""Runner resume_review / resume_documentation delegation tests (plan task 6).

The page-workflow decision must be applied through ``runner.resume_review``
BEFORE the outer graph resumes, and through ``runner.resume_documentation``
for the request-changes path, while staying a no-op for any run that has no
escalated page state (writer/review runs use plain fake graphs with no nodes).
"""

from __future__ import annotations

from types import SimpleNamespace

from strands.multiagent.base import Status

import draftly.workflows.runner as runner_mod
from draftly.agents.documentation.writer import WriterFactory
from draftly.orchestration.nodes.rubric_grader import build_docs_rubric_grader
from draftly.orchestration.page_workflow.handlers import (
    CrossPageReviewHandler,
    PageEvaluatorHandler,
    PageWriterHandler,
)
from draftly.orchestration.page_workflow.node import DocumentationWorkflowNode
from draftly.orchestration.page_workflow.repository import PageWorkflowRepository
from draftly.persistence.repositories.drafts import DraftRepository
from draftly.workflows.context import WorkflowContext
from tests.unit.orchestration.page_workflow.test_repository import FakeClient
from tests.workflows.test_documentation_page_resume import (
    IMPACT_NO_EVIDENCE,
    _invoke,
)

EVENT = {
    "event_id": "run-1",
    "event_type": "pull_request.opened",
    "source": "github",
    "project_id": "org-1",
    "repository": "acme/api",
    "pull_request": {"number": 1, "head": {"sha": "abc123"}},
}


def _wire_documentation_node():
    database = FakeClient()
    pages = PageWorkflowRepository(database=database)
    drafts = DraftRepository(database=database)

    def sealing_builder(model, tools, runtime=None, agent_id=None, node_id=None):
        del model, tools, runtime, agent_id, node_id

        class _SealingWriter:
            async def invoke_async(self, prompt, invocation_state=None, **kwargs):
                del prompt, kwargs
                state = invocation_state or {}
                page_id = state["page_id"]
                version = int(state["artifact_version"])
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

    node = DocumentationWorkflowNode(
        "document",
        repository=pages,
        evaluation_concurrency=1,
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
                reviewer_factory=lambda: SimpleNamespace(),
                drafts_repo=drafts,
                page_repository=pages,
            ),
        },
    )
    return node, pages


class _DocReviewGraph:
    """Minimal graph surface: a wired document node plus the resume lifecycle."""

    _resume_from_session = True

    def __init__(self, node: DocumentationWorkflowNode, invoke_status: Status) -> None:
        self.id = "graph"
        self.nodes = {"document": SimpleNamespace(executor=node)}
        self._interrupt_state = SimpleNamespace(activated=True, interrupts={"int-1"})
        self.session_manager = SimpleNamespace(
            _is_new_session=False,
            session_id="draftly-run-1",
            session_repository=SimpleNamespace(
                read_multi_agent=lambda session_id, graph_id: {"next_nodes_to_execute": ["review"]}
            ),
        )
        self.invoke_status = invoke_status

    async def invoke_async(self, resume_input, invocation_state=None):
        assert invocation_state is not None
        return SimpleNamespace(status=self.invoke_status, interrupts=[])


class _PlainGraph:
    """A non-documentation graph: no ``nodes`` dict at all."""

    _resume_from_session = True

    def __init__(self) -> None:
        self.id = "graph"
        self._interrupt_state = SimpleNamespace(activated=True, interrupts={"int-1"})
        self.session_manager = SimpleNamespace(
            _is_new_session=False,
            session_id="draftly-run-1",
            session_repository=SimpleNamespace(
                read_multi_agent=lambda session_id, graph_id: {"next_nodes_to_execute": ["review"]}
            ),
        )

    async def invoke_async(self, resume_input, invocation_state=None):
        return SimpleNamespace(status=Status.INTERRUPTED, interrupts=[])


async def test_resume_review_approve_delegates_before_outer_resume() -> None:
    node, pages = _wire_documentation_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-1")
    states = await pages.get_page_states(run_id="run-1")
    assert states[0].status == "awaiting_human_review"

    calls = []

    class _RecordingGraph(_DocReviewGraph):
        async def invoke_async(self, resume_input, invocation_state=None):
            calls.append("graph")
            return await super().invoke_async(resume_input, invocation_state)

    runner = runner_mod.WorkflowRunner(
        WorkflowContext(),
        graph_factory=lambda run_id, surface: _RecordingGraph(node, Status.INTERRUPTED),
        publisher=None,
    )
    await runner.resume_review(event=dict(EVENT), interrupt_id="int-1", response={"approved": True})

    # Delegation ran before the graph resume and accepted the artifacts.
    assert calls == ["graph"]
    states = await pages.get_page_states(run_id="run-1")
    assert states[0].status == "passed"


async def test_resume_review_reject_delegates_and_fails_pages() -> None:
    node, pages = _wire_documentation_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-1")
    runner = runner_mod.WorkflowRunner(
        WorkflowContext(),
        graph_factory=lambda run_id, surface: _DocReviewGraph(node, Status.INTERRUPTED),
        publisher=None,
    )
    await runner.resume_review(
        event=dict(EVENT),
        interrupt_id="int-1",
        response={"approved": False, "comment": "out of scope"},
    )
    states = await pages.get_page_states(run_id="run-1")
    assert states[0].status == "failed"
    assert "rejected by human review" in states[0].escalation_reason


async def test_resume_review_is_a_noop_for_non_documentation_runs() -> None:
    runner = runner_mod.WorkflowRunner(
        WorkflowContext(),
        graph_factory=lambda run_id, surface: _PlainGraph(),
        publisher=None,
    )
    state = await runner.resume_review(
        event=dict(EVENT), interrupt_id="int-1", response={"approved": True}
    )
    assert state is not None


async def test_resume_documentation_request_changes_schedules_revision() -> None:
    node, pages = _wire_documentation_node()
    await _invoke(node, IMPACT_NO_EVIDENCE, "run-3")
    runner = runner_mod.WorkflowRunner(
        WorkflowContext(),
        graph_factory=lambda run_id, surface: SimpleNamespace(
            nodes={"document": SimpleNamespace(executor=node)}
        ),
        publisher=None,
    )
    result = await runner.resume_documentation(
        event=dict(EVENT, event_id="run-3"),
        decision="request_changes",
        comment="add a rationale",
    )
    assert result.passed is False
    tasks = await pages.get_tasks(run_id="run-3")
    assert any(t.input_data.get("human_guided") and t.artifact_version == 2 for t in tasks)


async def test_resume_documentation_is_a_noop_without_escalation() -> None:
    node, pages = _wire_documentation_node()
    # Nothing seeded under this run: there is nothing to resume.
    runner = runner_mod.WorkflowRunner(
        WorkflowContext(),
        graph_factory=lambda run_id, surface: SimpleNamespace(
            nodes={"document": SimpleNamespace(executor=node)}
        ),
        publisher=None,
    )
    result = await runner.resume_documentation(
        event=dict(EVENT, event_id="run-4"),
        decision="request_changes",
        comment="add a rationale",
    )
    assert result is None
    assert not await pages.get_page_states(run_id="run-4")
