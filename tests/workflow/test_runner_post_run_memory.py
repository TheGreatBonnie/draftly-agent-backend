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
async def test_context_memory_bundle_exposes_scoped_sources():
    calls: list[tuple[str, str | None]] = []

    class Memory:
        async def recall_knowledge(self, query, *, limit, org_id):
            calls.append(("knowledge", org_id))
            return []

    class Episodes:
        async def find_similar(self, query, *, org_id, limit):
            calls.append(("episodes", org_id))
            return []

    class Procedures:
        async def match(self, query, *, org_id, limit):
            calls.append(("procedures", org_id))
            return []

    context = runner_mod.WorkflowContext(
        memory=Memory(),
        episodic=Episodes(),
        procedural=Procedures(),
    )
    bundle = context.memory_bundle()

    await bundle.knowledge("task", org_id="org-1")
    await bundle.episodes("task", org_id="org-1")
    await bundle.procedures("task", org_id="org-1")

    assert calls == [
        ("knowledge", "org-1"),
        ("episodes", "org-1"),
        ("procedures", "org-1"),
    ]


@pytest.mark.asyncio
async def test_post_run_memory_swallows_errors():
    async def boom(context, state, surface):
        raise RuntimeError("x")

    state = WorkflowState(run_id="r", event={})
    await runner_mod._post_run_memory(None, state, "github_pr", hook=boom)
