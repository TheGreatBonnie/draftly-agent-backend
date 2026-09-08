from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowStatus


@dataclass
class FakeEventsRepo:
    rows: dict[str, str] = field(default_factory=dict)

    async def find_by_event_id(self, event_id):
        status = self.rows.get(event_id)
        if status is None:
            return None
        return {"event_id": event_id, "status": status}


def make_context(**overrides: Any) -> WorkflowContext:
    jobs = MagicMock()
    jobs.update_status = AsyncMock()
    workflows = MagicMock()
    workflows.update_status = AsyncMock()
    return WorkflowContext(
        repositories=MagicMock(
            jobs=jobs,
            events=overrides.pop("events", None),
            github_workflows=workflows,
        ),
        **overrides,
    )


async def test_sets_running_then_completed() -> None:
    import draftly.workflows.documentation.github_pr_workflow as mod
    from draftly.workflows.documentation.github_pr_workflow import (
        run_pull_request_workflow,
    )

    context = make_context()

    class FalseRunner:
        def __init__(self, context, *, publisher=None):
            self.publisher = publisher

        async def run(self, event):
            state = MagicMock()
            state.run_id = "ev-1"
            state.status = WorkflowStatus.DELIVERED
            return state

    mod.WorkflowRunner = FalseRunner

    state = await run_pull_request_workflow(
        context, {"event_id": "ev-1"}, run_id="ev-1"
    )

    assert state.status == WorkflowStatus.DELIVERED
    calls = context.repositories.jobs.update_status.await_args_list
    assert any(c.kwargs["status"] == "running" for c in calls)
    assert any(c.kwargs["status"] == "completed" for c in calls)


async def test_sets_failed_on_failed_status() -> None:
    import draftly.workflows.documentation.github_pr_workflow as mod
    from draftly.workflows.documentation.github_pr_workflow import (
        run_pull_request_workflow,
    )

    context = make_context()

    class FailRunner:
        def __init__(self, context, *, publisher=None):
            pass

        async def run(self, event):
            state = MagicMock()
            state.run_id = "ev-f"
            state.status = WorkflowStatus.FAILED
            return state

    mod.WorkflowRunner = FailRunner
    await run_pull_request_workflow(context, {"event_id": "ev-f"}, run_id="ev-f")
    calls = context.repositories.jobs.update_status.await_args_list
    assert any(c.kwargs["status"] == "failed" for c in calls)


async def test_duplicate_and_pending_review_do_not_mark_completed() -> None:
    import draftly.workflows.documentation.github_pr_workflow as mod
    from draftly.workflows.documentation.github_pr_workflow import (
        run_pull_request_workflow,
    )

    for status in (WorkflowStatus.DUPLICATE, WorkflowStatus.PENDING_REVIEW, WorkflowStatus.SKIPPED):
        context = make_context()

        class DupRunner:
            def __init__(self, context, *, publisher=None):
                pass

            async def run(self, event):
                state = MagicMock()
                state.run_id = "ev-d"
                state.status = status
                return state

        mod.WorkflowRunner = DupRunner
        await run_pull_request_workflow(context, {"event_id": "ev-d"}, run_id="ev-d")
        calls = context.repositories.jobs.update_status.await_args_list
        # Only "running" should be set, never "completed" for these intermediate/skip statuses
        assert any(c.kwargs["status"] == "running" for c in calls)
        assert not any(c.kwargs["status"] == "completed" for c in calls)


async def test_passes_publisher_to_runner() -> None:
    from draftly.workflows.documentation.github_pr_workflow import (
        run_pull_request_workflow,
    )

    publisher = object()

    class CaptureRunner:
        captured = None

        def __init__(self, context, *, publisher=None):
            CaptureRunner.captured = publisher

        async def run(self, event):
            state = MagicMock()
            state.run_id = "ev-2"
            state.status = WorkflowStatus.DELIVERED
            return state

    import draftly.workflows.documentation.github_pr_workflow as mod

    mod.WorkflowRunner = CaptureRunner

    context = make_context(publisher=publisher)
    await run_pull_request_workflow(context, {"event_id": "ev-2"}, run_id="ev-2")

    assert CaptureRunner.captured is publisher


async def test_duplicate_replay_reconciles_terminal_status() -> None:
    import draftly.workflows.documentation.github_pr_workflow as mod
    from draftly.workflows.documentation.github_pr_workflow import (
        run_pull_request_workflow,
    )

    context = make_context(events=FakeEventsRepo(rows={"ev-d": "completed"}))

    class DupRunner:
        def __init__(self, context, *, publisher=None):
            pass

        async def run(self, event):
            state = MagicMock()
            state.run_id = "ev-d"
            state.status = WorkflowStatus.DUPLICATE
            return state

    mod.WorkflowRunner = DupRunner
    await run_pull_request_workflow(context, {"event_id": "ev-d"}, run_id="ev-d")

    calls = context.repositories.jobs.update_status.await_args_list
    assert any(c.kwargs["status"] == "completed" for c in calls)
