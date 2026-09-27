"""The executor installs the shared read cache around its fan-out.

The cache must be installed BEFORE ``asyncio.gather`` spawns the writer tasks.
asyncio copies a task's context at creation, so a cache set after the spawn
would reach only the loop's own context and every page would get its own -
silently disabling cross-page deduplication while every other test still passed.

This is the integration-level guard for that invariant; the unit-level one lives
in ``test_repo_read_cache.py::test_concurrent_tasks_share_one_cache_instance``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from draftly.agents.documentation.repo_read_cache import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    current_repo_read_cache,
)
from draftly.orchestration.page_workflow.executor import PageWorkflowExecutor
from draftly.orchestration.page_workflow.repository import WorkflowTask

from .test_executor import RUN, FakeTaskStore

PAGES = ("docs/a.md", "docs/b.md", "docs/c.md")


async def _seed_writes(store: FakeTaskStore) -> None:
    for page_id in PAGES:
        await store.enqueue(
            run_id=RUN,
            task_id=f"write:{page_id}:1",
            org_id="org-1",
            task_type="write",
            page_id=page_id,
            artifact_version=1,
        )


def _observing_handler(seen: list, gate: asyncio.Event) -> Any:
    """A write handler that records the cache it observes.

    ``gate`` holds every writer open at once so the three tasks genuinely
    overlap. Without it the first task can finish before the third starts and
    the test passes even when sharing is broken.
    """

    async def handle(task: WorkflowTask) -> dict:
        seen.append(current_repo_read_cache())
        gate.set()
        await gate.wait()
        return {"task_id": task.task_id}

    return handle


@pytest.mark.asyncio
async def test_all_write_tasks_share_one_cache_instance() -> None:
    store = FakeTaskStore()
    await _seed_writes(store)
    seen: list = []
    gate = asyncio.Event()

    executor = PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers={"write": _observing_handler(seen, gate)},
        write_concurrency=3,
        evaluation_concurrency=1,
    )

    result = await executor.run(run_id=RUN, org_id="org-1")

    assert len(result.completed_task_ids) == len(PAGES)
    assert len(seen) == len(PAGES)
    assert all(c is not None for c in seen), "no cache was installed for the writers"
    assert len({id(c) for c in seen}) == 1, "each page got its own cache"


@pytest.mark.asyncio
async def test_the_cache_is_torn_down_after_the_run() -> None:
    """A leaked cache would serve one run's reads to the next."""
    store = FakeTaskStore()
    await _seed_writes(store)
    seen: list = []
    gate = asyncio.Event()

    executor = PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers={"write": _observing_handler(seen, gate)},
        write_concurrency=3,
        evaluation_concurrency=1,
    )
    await executor.run(run_id=RUN, org_id="org-1")

    assert current_repo_read_cache() is None


@pytest.mark.asyncio
async def test_a_failing_run_also_tears_the_cache_down() -> None:
    store = FakeTaskStore()
    await _seed_writes(store)

    async def handle(task: WorkflowTask) -> dict:
        raise RuntimeError("infrastructure is down")

    executor = PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers={"write": handle},
        write_concurrency=3,
        evaluation_concurrency=1,
    )
    result = await executor.run(run_id=RUN, org_id="org-1")

    assert result.failed_task_ids
    assert current_repo_read_cache() is None


@pytest.mark.asyncio
async def test_the_cache_is_bounded_and_reported() -> None:
    """The executor sizes the cache; it must not be unbounded."""
    store = FakeTaskStore()
    await _seed_writes(store)
    caches: list = []
    gate = asyncio.Event()

    async def handle(task: WorkflowTask) -> dict:
        cache = current_repo_read_cache()
        if cache is not None:
            caches.append(cache)
            cache.put(task.task_id, "x" * 32)
        gate.set()
        await gate.wait()
        return {"task_id": task.task_id}

    executor = PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers={"write": handle},
        write_concurrency=3,
        evaluation_concurrency=1,
    )
    await executor.run(run_id=RUN, org_id="org-1")

    assert caches, "no cache to inspect"
    stats = caches[0].stats()
    assert stats["entries"] <= DEFAULT_MAX_ENTRIES
    assert stats["bytes"] <= DEFAULT_MAX_BYTES
