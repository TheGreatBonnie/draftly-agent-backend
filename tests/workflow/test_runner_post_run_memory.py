"""Runner post-run memory hook tests."""

import pytest

import draftly.workflows.runner as runner_mod
from draftly.workflows.state import WorkflowState


def test_context_has_memory_dep_fields():
    ctx = runner_mod.WorkflowContext()
    for field in ("episodic", "procedural", "docgraph", "candidates"):
        assert getattr(ctx, field) is None


def test_post_run_hook_exists():
    assert hasattr(runner_mod, "_post_run_memory")


@pytest.mark.asyncio
async def test_post_run_memory_swallows_errors():
    async def boom(context, state, surface):
        raise RuntimeError("x")

    state = WorkflowState(run_id="r", event={})
    await runner_mod._post_run_memory(None, state, "github_pr", hook=boom)
