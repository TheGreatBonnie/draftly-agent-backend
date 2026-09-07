from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowStatus


async def test_release_workflow_mirrors_terminal_job_status() -> None:
    import draftly.workflows.documentation.github_release_workflow as mod

    jobs = MagicMock()
    jobs.update_status = AsyncMock()
    context = WorkflowContext(repositories=MagicMock(jobs=jobs))

    class Runner:
        def __init__(self, context, *, publisher=None):
            pass

        async def run(self, event):
            state = MagicMock(run_id="rel-1", status=WorkflowStatus.DELIVERED)
            return state

    mod.WorkflowRunner = Runner
    state = await mod.run_release_workflow(
        context, {"event_id": "rel-1"}, run_id="rel-1"
    )

    assert state.status == WorkflowStatus.DELIVERED
    calls = jobs.update_status.await_args_list
    assert any(call.kwargs["status"] == "running" for call in calls)
    assert any(call.kwargs["status"] == "completed" for call in calls)
