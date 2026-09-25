from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from strands.multiagent.base import Status

from draftly.workflows.runner import WorkflowRunner


class FakeBroadcaster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def broadcast(self, org_id: str, event_type: str, payload: dict) -> bool:
        self.calls.append((org_id, event_type, payload))
        return True


def make_result(status: Status) -> SimpleNamespace:
    return SimpleNamespace(status=status, interrupts=[], execution_order=[], failed_nodes=0)


def make_context(broadcaster: FakeBroadcaster) -> SimpleNamespace:
    return SimpleNamespace(
        events=SimpleNamespace(
            try_claim=async_claim,
            mark_status=async_mark,
        ),
        reviews=None,
        routing_decision=None,
        broadcaster=broadcaster,
        publisher=None,
        review_policy=lambda: None,
        model="x",
    )


async def async_claim(*args: Any, **kwargs: Any) -> bool:
    return True


async def async_mark(*args: Any, **kwargs: Any) -> None:
    return None


async def test_runner_broadcasts_running_and_completed():
    broadcaster = FakeBroadcaster()
    context = make_context(broadcaster)

    def graph_factory(run_id: str, surface: str):
        class G:
            async def invoke_async(self, task: Any, invocation_state: dict | None = None):
                return make_result(Status.COMPLETED)

        return G()

    runner = WorkflowRunner(context, graph_factory=graph_factory)
    await runner.run(
        {
            "event_id": "ev-1",
            "event_type": "pull_request.opened",
            "project_id": "org-9",
            "source": "github",
        }
    )

    types = [c[1] for c in broadcaster.calls]
    assert "workflow:changed" in types
    running = [c for c in broadcaster.calls if c[2]["status"] == "running"]
    completed = [c for c in broadcaster.calls if c[2]["status"] == "completed"]
    assert running and running[0][0] == "org-9"
    assert completed


async def test_runner_broadcasts_failed_on_failed_result():
    broadcaster = FakeBroadcaster()
    context = make_context(broadcaster)

    def graph_factory(run_id: str, surface: str):
        class G:
            async def invoke_async(self, task: Any, invocation_state: dict | None = None):
                return make_result(Status.FAILED)

        return G()

    runner = WorkflowRunner(context, graph_factory=graph_factory)
    await runner.run(
        {
            "event_id": "ev-2",
            "event_type": "pull_request.opened",
            "project_id": "org-9",
            "source": "github",
        }
    )

    statuses = [c[2]["status"] for c in broadcaster.calls]
    assert "failed" in statuses


async def test_runner_skips_broadcast_without_project_id():
    broadcaster = FakeBroadcaster()
    context = make_context(broadcaster)

    def graph_factory(run_id: str, surface: str):
        class G:
            async def invoke_async(self, task: Any, invocation_state: dict | None = None):
                return make_result(Status.COMPLETED)

        return G()

    runner = WorkflowRunner(context, graph_factory=graph_factory)
    await runner.run({"event_id": "ev-3", "event_type": "push", "source": "github"})
    assert broadcaster.calls == []  # no org → no broadcast


async def test_streaming_node_transitions_refresh_dashboard():
    broadcaster = FakeBroadcaster()
    context = make_context(broadcaster)
    class Publisher:
        async def publish(self, envelope):
            return None
    class Graph:
        async def stream_async(self, task, invocation_state):
            yield {"type": "multiagent_node_start", "node_id": "research"}
            yield {"type": "multiagent_node_stop", "node_id": "research",
                   "node_result": {"status": "COMPLETED"}}
            yield {"result": make_result(Status.COMPLETED)}
    runner = WorkflowRunner(context, publisher=Publisher())
    await runner._invoke_streaming(
        Graph(), "task", {"run_id": "run-1", "project_id": "org-9"}, "pull_request"
    )
    progress = [call for call in broadcaster.calls if call[2].get("node_id") == "research"]
    assert len(progress) == 2
    assert all(call[1] == "workflow:changed" and call[0] == "org-9" for call in progress)
