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
            "event_type": "pull_request.merged",
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
            "event_type": "pull_request.merged",
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
