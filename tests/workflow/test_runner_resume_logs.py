"""Runner resume_review lifecycle logging tests."""

from types import SimpleNamespace

import pytest
import structlog
from strands.multiagent.base import Status

import draftly.workflows.runner as runner_mod
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowStatus

EVENT = {
    "event_id": "run-1",
    "event_type": "pull_request.opened",
    "source": "github",
    "project_id": "org-1",
    "pull_request": {"number": 1},
}


class _InterruptGraph:
    _resume_from_session = True

    def __init__(self) -> None:
        self.id = "graph"
        self._interrupt_state = SimpleNamespace(activated=True, interrupts={"int-1"})
        self.session_manager = SimpleNamespace(
            _is_new_session=False,
            session_id="draftly-run-1",
            session_repository=SimpleNamespace(
                read_multi_agent=lambda session_id, graph_id: {
                    "next_nodes_to_execute": ["review"]
                }
            ),
        )

    async def invoke_async(self, resume_input, invocation_state=None):
        return SimpleNamespace(status=Status.INTERRUPTED, interrupts=[])


class _FailingGraph:
    _resume_from_session = True

    def __init__(self) -> None:
        self.id = "graph"
        self._interrupt_state = SimpleNamespace(activated=True, interrupts={"int-1"})
        self.session_manager = SimpleNamespace(
            _is_new_session=False,
            session_id="draftly-run-1",
            session_repository=SimpleNamespace(
                read_multi_agent=lambda session_id, graph_id: {
                    "next_nodes_to_execute": ["review"]
                }
            ),
        )

    async def invoke_async(self, resume_input, invocation_state=None):
        raise RuntimeError("graph down")


@pytest.mark.asyncio
async def test_resume_started_and_done_logged_on_review_interrupt(monkeypatch):
    from structlog.testing import capture_logs

    runner = runner_mod.WorkflowRunner(
        WorkflowContext(),
        graph_factory=lambda run_id, surface: _InterruptGraph(),
        publisher=None,
    )

    with capture_logs() as logs:
        monkeypatch.setattr(
            runner_mod, "logger", structlog.get_logger("test.resume_interrupted")
        )
        state = await runner.resume_review(
            event=dict(EVENT), interrupt_id="int-1", response={"approved": True}
        )

    markers = {line.get("event") for line in logs}
    assert "workflow_resume_started" in markers
    done = [line for line in logs if line.get("event") == "workflow_resume_done"]
    assert len(done) == 1
    assert done[0]["run_id"] == "run-1"
    assert done[0]["status"] == state.status.value
    assert state.status == WorkflowStatus.PENDING_REVIEW


@pytest.mark.asyncio
async def test_resume_started_and_failed_logged_on_graph_error(monkeypatch):
    from structlog.testing import capture_logs

    runner = runner_mod.WorkflowRunner(
        WorkflowContext(),
        graph_factory=lambda run_id, surface: _FailingGraph(),
        publisher=None,
    )

    with capture_logs() as logs:
        monkeypatch.setattr(
            runner_mod, "logger", structlog.get_logger("test.resume_failed")
        )
        state = await runner.resume_review(
            event=dict(EVENT),
            interrupt_id="int-1",
            response={"approved": False, "comment": "no"},
        )

    markers = {line.get("event") for line in logs}
    assert "workflow_resume_started" in markers
    failed = [line for line in logs if line.get("event") == "workflow_review_resume_failed"]
    assert len(failed) == 1
    assert failed[0]["run_id"] == "run-1"
    assert state.status == WorkflowStatus.FAILED
