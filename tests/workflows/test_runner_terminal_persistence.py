"""Runner terminal persistence: jobs/github_workflows go terminal before the
events row, and a failed events mark never aborts a delivered/failed run."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from strands.multiagent.base import MultiAgentResult, Status

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner

PR_EVENT = {
    "event_id": "evt-1",
    "event_type": "pull_request.opened",
    "repository": "acme/api",
    "actor": "dev",
    "source": "github",
    "project_id": "org-1",
}

# A single shared list so ordering (jobs-before-events for the terminal write)
# is comparable across writers; the claim entry is inert to the assertion.
Timeline = list[tuple[str, str]]


@dataclass
class FakeEventsRepo:
    timeline: Timeline = field(default_factory=list)
    failing: bool = False

    async def try_claim(self, event_id, **kwargs):
        self.timeline.append(("claim", event_id))
        return True

    async def find_by_event_id(self, event_id):
        return {"event_id": event_id, "status": "running"}

    async def mark_status(self, event_id, status):
        if self.failing:
            raise RuntimeError("events db down")
        self.timeline.append(("events", status))


@dataclass
class FakeJobsRepo:
    timeline: Timeline = field(default_factory=list)

    async def update_status(self, **kwargs):
        self.timeline.append(("jobs", kwargs["status"]))


@dataclass
class FakeGitHubWorkflowsRepo:
    timeline: Timeline = field(default_factory=list)

    async def update_status(self, **kwargs):
        self.timeline.append(("workflows", kwargs["status"]))


class FakeGraph:
    def __init__(self, result):
        self.result = result

    async def invoke_async(self, task, invocation_state=None, **kwargs):
        return self.result


def make_context(events: FakeEventsRepo | None = None) -> WorkflowContext:
    timeline: Timeline = []
    events = events or FakeEventsRepo(timeline=timeline)
    return WorkflowContext(
        repositories=type(
            "Repos", (), {
                "events": events,
                "jobs": FakeJobsRepo(timeline=timeline),
                "github_workflows": FakeGitHubWorkflowsRepo(timeline=timeline),
            }
        )()
    )


def completed_result() -> MultiAgentResult:
    return MultiAgentResult(status=Status.COMPLETED)


def failed_result() -> MultiAgentResult:
    result = MultiAgentResult(status=Status.FAILED)
    result.failed_nodes = 1
    result.execution_order = [SimpleNamespace(node_id="update", execution_status=Status.FAILED)]
    return result


async def run_result(result: MultiAgentResult, events: FakeEventsRepo | None = None):
    context = make_context(events=events)
    runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: FakeGraph(result))
    state = await runner.run(dict(PR_EVENT))
    return state, context


def _terminal_index(timeline: Timeline, who: str, status: str) -> int:
    return next(i for i, (w, s) in enumerate(timeline) if w == who and s == status)


async def test_completed_writes_lifecycle_before_events_mark() -> None:
    status, context = await run_result(completed_result())

    assert status.status.value == "delivered"
    timeline = context.repositories.events.timeline
    assert _terminal_index(timeline, "jobs", "completed") < _terminal_index(timeline, "events", "completed")  # noqa: E501


async def test_failed_writes_lifecycle_before_events_mark() -> None:
    status, context = await run_result(failed_result())

    assert status.status.value == "failed"
    timeline = context.repositories.events.timeline
    assert _terminal_index(timeline, "jobs", "failed") < _terminal_index(timeline, "events", "failed")  # noqa: E501


async def test_events_mark_failure_does_not_abort_completed_run() -> None:
    status, context = await run_result(completed_result(), events=FakeEventsRepo(failing=True))

    assert status.status.value == "delivered"
    assert ("jobs", "completed") in context.repositories.jobs.timeline


async def test_events_mark_failure_does_not_abort_failed_run() -> None:
    status, context = await run_result(failed_result(), events=FakeEventsRepo(failing=True))

    assert status.status.value == "failed"
    assert ("jobs", "failed") in context.repositories.jobs.timeline
