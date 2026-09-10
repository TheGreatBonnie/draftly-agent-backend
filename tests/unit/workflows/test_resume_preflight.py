"""§resume-session-store: preflight guard in WorkflowRunner.resume_review.

When the interrupted session state is missing (a fresh session is created
during the resume request), the guard must block invocation, re-queue the
review as pending, and raise a clear error instead of letting Strands run the
resume payload as a brand-new task through the entry-point nodes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from strands.multiagent.base import Status

from draftly.review.resume import ReviewResumeError
from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner


class StubInterruptState:
    def __init__(self, activated: bool, interrupts: set[str]) -> None:
        self.activated = activated
        self.interrupts = interrupts


def _fake_manager(*, resumable: bool, interrupts: Any = None) -> Any:
    payload = None
    if resumable:
        state: dict[str, Any] = {"next_nodes_to_execute": ["review"]}
        if interrupts is not None:
            state["_internal_state"] = {
                "interrupt_state": {"interrupts": list(interrupts)}
            }
        payload = state
    repo = SimpleNamespace(
        read_multi_agent=lambda session_id, graph_id: payload
    )
    return SimpleNamespace(
        _is_new_session=not resumable,
        session_id="fake-session",
        session_repository=repo,
    )


class StubGraph:
    def __init__(
        self, *, resumable: bool, interrupt_state: Any, interrupts: Any = None
    ) -> None:
        self.id = "fake-graph"
        self._interrupt_state = interrupt_state
        self.session_manager = _fake_manager(resumable=resumable, interrupts=interrupts)

    async def invoke_async(self, resume_input: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise AssertionError("invoke_async must not be called when the guard blocks")


class StubInvokeGraph(StubGraph):
    async def invoke_async(self, resume_input: list[dict[str, Any]], **kwargs: Any) -> Any:
        return SimpleNamespace(
            status=Status.COMPLETED,
            result=SimpleNamespace(metrics=None),
            execution_order=[],
            interrupts=[],
        )


class FakeRepositories:
    def __init__(self) -> None:
        self.events = SimpleNamespace(
            mark_status=self._mark_status,
        )
        self.recorded_status: list[tuple[str, str]] = []
        self.jobs = SimpleNamespace(update_status=self._update_status)
        self.job_updates: list[dict[str, Any]] = []
        self.github_workflows = SimpleNamespace(
            update_status=self._update_workflow_status
        )
        self.workflow_updates: list[str] = []
        self.routing = SimpleNamespace(record=self._record_routing)
        self.routing_rows: list[dict[str, Any]] = []

    async def _mark_status(self, event_id: str, status: str) -> None:
        self.recorded_status.append((event_id, status))

    async def _update_status(
        self,
        *,
        job_id: str,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.job_updates.append(
            {"job_id": job_id, "status": status, "error": error, "result": result}
        )

    async def _update_workflow_status(self, workflow_id: str, status: str) -> None:
        self.workflow_updates.append((workflow_id, status))

    async def _record_routing(self, row: dict[str, Any]) -> None:
        self.routing_rows.append(row)


class FakeNotifier:
    def __init__(self) -> None:
        self.notified: list[str] = []

    async def notify_reviewers(self, run_id: str) -> dict[str, list[str]]:
        self.notified.append(run_id)
        return {"slack": ["user-1"]}


class FakeBroadcaster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def broadcast(self, org_id: str, channel: str, payload: dict[str, Any]) -> None:
        self.calls.append((org_id, channel, payload))


def _event() -> dict[str, Any]:
    return {
        "event_id": "run-1",
        "event_type": "pull_request.merged",
        "project_id": "org-1",
        "installation_id": 42,
        "repository": "acme/docs",
    }


def _runner(
    graph: StubGraph,
    repositories: FakeRepositories,
) -> WorkflowRunner:
    context = WorkflowContext(
        repositories=repositories,
        model=None,
        storage_dir=".draftly/sessions",
    )
    context.broadcaster = FakeBroadcaster()
    context.notifier = FakeNotifier()
    runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: graph)
    return runner


async def test_resume_blocks_when_session_state_missing():
    graph = StubGraph(
        resumable=False,
        interrupt_state=StubInterruptState(activated=False, interrupts=set()),
    )
    repositories = FakeRepositories()
    runner = _runner(graph, repositories)

    with pytest.raises(ReviewResumeError, match="session"):
        await runner.resume_review(
            event=_event(),
            interrupt_id="v1:before_node_call:int-1",
            response={"approved": True, "comment": "ship it"},
        )

    assert ("run-1", "pending_review") in repositories.recorded_status
    assert repositories.job_updates
    assert repositories.job_updates[-1]["status"] == "pending_review"
    assert repositories.job_updates[-1]["result"]["status"] == "SESSION_LOST"
    assert ("run-1", "pending_review") in repositories.workflow_updates
    assert context_notified(runner) == ["run-1"]
    assert context_broadcast(runner) != []
    assert context_broadcast(runner)[0][2]["status"] == "pending_review"


async def test_resume_blocks_when_interrupt_id_not_in_restored_state():
    graph = StubGraph(
        resumable=True,
        interrupt_state=StubInterruptState(
            activated=True, interrupts={"v1:before_node_call:int-other"}
        ),
        interrupts={"v1:before_node_call:int-other"},
    )
    repositories = FakeRepositories()
    runner = _runner(graph, repositories)

    with pytest.raises(ReviewResumeError, match="session"):
        await runner.resume_review(
            event=_event(),
            interrupt_id="v1:before_node_call:int-1",
            response={"approved": True, "comment": "ship it"},
        )

    assert ("run-1", "pending_review") in repositories.recorded_status


async def test_resume_invokes_when_session_state_restored():
    graph = StubInvokeGraph(resumable=True, interrupt_state=StubInterruptState(
            activated=True,
            interrupts={"v1:before_node_call:int-1"},
        ),
    )
    repositories = FakeRepositories()
    runner = _runner(graph, repositories)

    state = await runner.resume_review(
        event=_event(),
        interrupt_id="v1:before_node_call:int-1",
        response={"approved": True, "comment": "ship it"},
    )

    assert repositories.recorded_status == [("run-1", "completed")]
    assert ("run-1", "completed") in repositories.workflow_updates
    assert state.status.value == "delivered"


def context_notified(runner: WorkflowRunner) -> list[str]:
    return runner.context.notifier.notified


def context_broadcast(runner: WorkflowRunner) -> list[Any]:
    return runner.context.broadcaster.calls
