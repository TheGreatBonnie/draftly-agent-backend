"""PageWorkflowExecutor scheduling, lease, retry, and deadlock tests.

The executor runs against an in-memory store that mirrors the
``PageWorkflowRepository`` task surface, so no database is required. A local
``ConcurrencyTracker`` records handler call counts plus start/finish times so
the tests can assert overlap, wait-for-dependency, and peak-concurrency
behavior without the production executor knowing about the tracker.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from draftly.orchestration.page_workflow.executor import (
    DeadlockedWorkflowError,
    ExecutorResult,
    PageWorkflowExecutor,
    TaskHandler,
)
from draftly.orchestration.page_workflow.repository import StaleArtifactError, WorkflowTask

RUN = "run-1"


class ConcurrencyTracker:
    """Records handler invocations, start/finish times, and peak concurrency."""

    def __init__(self) -> None:
        self.handler_calls: dict[str, int] = {}
        self.started_at: dict[str, float] = {}
        self.finished_at: dict[str, float] = {}
        self.active: dict[str, int] = {}
        self.max_active: dict[str, int] = {}

    def start(self, key: str, group: str) -> None:
        self.handler_calls[key] = self.handler_calls.get(key, 0) + 1
        self.started_at[key] = time.monotonic()
        self.active[group] = self.active.get(group, 0) + 1
        self.max_active[group] = max(self.max_active.get(group, 0), self.active[group])

    def finish(self, key: str, group: str) -> None:
        self.active[group] -= 1
        self.finished_at[key] = time.monotonic()


class FakeTaskStore:
    """In-memory double of the PageWorkflowRepository task surface.

    Mirrors the SQL contracts of ``PageWorkflowRepository``: claims only mark
    pending tasks whose dependencies all completed, completions/retries require
    the running task's lease owner, and expired leases recycle to pending.
    """

    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.now = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)

    async def enqueue(
        self,
        *,
        run_id: str,
        task_id: str,
        org_id: str,
        task_type: str,
        page_id: str | None = None,
        artifact_version: int | None = None,
        dependencies: list[str] | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> None:
        self.tasks.append(
            {
                "run_id": run_id,
                "task_id": task_id,
                "org_id": org_id,
                "task_type": task_type,
                "page_id": page_id,
                "artifact_version": artifact_version,
                "dependencies": list(dependencies or []),
                "status": "pending",
                "infrastructure_retries": 0,
                "lease_owner": None,
                "lease_expires_at": None,
                "input_data": dict(input_data or {}),
                "output_data": None,
                "error": None,
                "created_at": self.now,
                "updated_at": self.now,
            }
        )

    async def claim_ready_tasks(
        self,
        *,
        run_id: str,
        lease_owner: str,
        lease_seconds: int = 300,
        limit: int = 10,
    ) -> list[WorkflowTask]:
        completed = {
            t["task_id"]
            for t in self.tasks
            if t["run_id"] == run_id and t["status"] == "completed"
        }
        ready = [
            t
            for t in self.tasks
            if t["run_id"] == run_id
            and t["status"] == "pending"
            and all(dep in completed for dep in t["dependencies"])
        ]
        ready.sort(key=lambda t: (t["created_at"], t["task_id"]))
        claimed = ready[:limit]
        for task in claimed:
            task["status"] = "running"
            task["lease_owner"] = lease_owner
            task["lease_expires_at"] = self.now + timedelta(seconds=lease_seconds)
            task["updated_at"] = self.now
        return [self._to_task(t) for t in claimed]

    async def complete_task(
        self,
        *,
        run_id: str,
        task_id: str,
        owner: str,
        output_data: dict[str, Any] | None = None,
    ) -> bool:
        task = self._find(run_id, task_id)
        if task is None or task["status"] != "running":
            return False
        if task["lease_owner"] != owner:
            raise StaleArtifactError(f"task {task_id!r} is leased by another worker")
        task["status"] = "completed"
        task["output_data"] = output_data
        task["updated_at"] = self.now
        return True

    async def retry_or_fail_task(
        self,
        *,
        run_id: str,
        task_id: str,
        owner: str,
        error: str,
    ) -> str | None:
        task = self._find(run_id, task_id)
        if task is None or task["status"] != "running":
            return None
        if task["lease_owner"] != owner:
            raise StaleArtifactError(f"task {task_id!r} is leased by another worker")
        if task["infrastructure_retries"] < 1:
            task["status"] = "pending"
        else:
            task["status"] = "failed"
        task["infrastructure_retries"] = min(task["infrastructure_retries"] + 1, 1)
        task["error"] = error
        task["lease_owner"] = None
        task["lease_expires_at"] = None
        task["updated_at"] = self.now
        return task["status"]

    async def reset_expired_leases(self, *, run_id: str) -> int:
        recycled = 0
        for task in self.tasks:
            if (
                task["run_id"] == run_id
                and task["status"] == "running"
                and task["lease_expires_at"] is not None
                and task["lease_expires_at"] < self.now
            ):
                task["status"] = "pending"
                task["lease_owner"] = None
                task["lease_expires_at"] = None
                task["updated_at"] = self.now
                recycled += 1
        return recycled

    async def get_tasks(self, *, run_id: str) -> list[WorkflowTask]:
        tasks = [t for t in self.tasks if t["run_id"] == run_id]
        tasks.sort(key=lambda t: (t["created_at"], t["task_id"]))
        return [self._to_task(t) for t in tasks]

    def _find(self, run_id: str, task_id: str) -> dict[str, Any] | None:
        return next(
            (t for t in self.tasks if t["run_id"] == run_id and t["task_id"] == task_id),
            None,
        )

    def claim_for(self, task_id: str) -> dict[str, Any]:
        return next(t for t in self.tasks if t["task_id"] == task_id)

    @staticmethod
    def _to_task(row: dict[str, Any]) -> WorkflowTask:
        return WorkflowTask(
            run_id=str(row["run_id"]),
            task_id=str(row["task_id"]),
            org_id=str(row["org_id"]),
            task_type=str(row["task_type"]),
            page_id=row["page_id"],
            artifact_version=row["artifact_version"],
            dependencies=list(row.get("dependencies") or []),
            status=str(row["status"]),
            infrastructure_retries=int(row.get("infrastructure_retries") or 0),
            lease_owner=row.get("lease_owner"),
            lease_expires_at=row.get("lease_expires_at"),
            input_data=dict(row.get("input_data") or {}),
            output_data=row.get("output_data"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


def _tracked_handler(
    tracker: ConcurrencyTracker,
    group: str,
    *,
    barrier: asyncio.Event | None = None,
    fail: Callable[[int], bool] | None = None,
) -> TaskHandler:
    """Return a TaskHandler that reports to ``tracker``.

    ``barrier`` gates the start of every invocation (used to force overlap);
    ``fail(call)`` returns True to raise for that 1-based invocation.
    """

    async def handle(task: WorkflowTask) -> dict[str, Any]:
        tracker.start(task.task_id, group)
        try:
            if fail is not None and fail(tracker.handler_calls[task.task_id]):
                raise RuntimeError(f"instrumented failure for {task.task_id}")
            if barrier is not None:
                barrier.set()
                await barrier.wait()
            await asyncio.sleep(0.01)
            return {"task_id": task.task_id, "group": group}
        finally:
            tracker.finish(task.task_id, group)

    return handle


async def _seed_page_run(store: FakeTaskStore, page_id: str) -> None:
    write_id = f"write:{page_id}:1"
    eval_id = f"evaluate:{page_id}:1"
    await store.enqueue(
        run_id=RUN,
        task_id=write_id,
        org_id="org-1",
        task_type="write",
        page_id=page_id,
        artifact_version=1,
    )
    await store.enqueue(
        run_id=RUN,
        task_id=eval_id,
        org_id="org-1",
        task_type="evaluate",
        page_id=page_id,
        artifact_version=1,
        dependencies=[write_id],
    )


def _executor(
    store: FakeTaskStore,
    tracker: ConcurrencyTracker,
    **kwargs: Any,
) -> PageWorkflowExecutor:
    return PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers={
            "write": _tracked_handler(tracker, "write"),
            "evaluate": _tracked_handler(tracker, "evaluation"),
        },
        **kwargs,
    )


async def test_independent_writes_overlap_and_evaluations_wait_for_writes() -> None:
    store = FakeTaskStore()
    await _seed_page_run(store, "docs/a.md")
    await _seed_page_run(store, "docs/b.md")
    tracker = ConcurrencyTracker()
    barrier = asyncio.Event()
    handlers = {
        "write": _tracked_handler(tracker, "write", barrier=barrier),
        "evaluate": _tracked_handler(tracker, "evaluation"),
    }
    executor = PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers=handlers,
        write_concurrency=2,
        evaluation_concurrency=2,
    )

    result = await executor.run(run_id=RUN, org_id="org-1")

    assert result.completed_task_ids == {
        "write:docs/a.md:1",
        "write:docs/b.md:1",
        "evaluate:docs/a.md:1",
        "evaluate:docs/b.md:1",
    }
    assert result.failed_task_ids == set()
    assert tracker.max_active["write"] == 2
    assert tracker.max_active["evaluation"] == 2
    assert (
        tracker.started_at["evaluate:docs/a.md:1"]
        >= tracker.finished_at["write:docs/a.md:1"]
    )
    assert (
        tracker.started_at["evaluate:docs/b.md:1"]
        >= tracker.finished_at["write:docs/b.md:1"]
    )


async def test_configured_concurrency_is_never_exceeded() -> None:
    store = FakeTaskStore()
    await _seed_page_run(store, "docs/a.md")
    await _seed_page_run(store, "docs/b.md")
    await store.enqueue(
        run_id=RUN,
        task_id="write:docs/c.md:1",
        org_id="org-1",
        task_type="write",
        page_id="docs/c.md",
        artifact_version=1,
    )
    tracker = ConcurrencyTracker()
    executor = _executor(store, tracker, write_concurrency=2, evaluation_concurrency=1)

    result = await executor.run(run_id=RUN, org_id="org-1")

    assert result.completed_task_ids == {
        "write:docs/a.md:1",
        "write:docs/b.md:1",
        "write:docs/c.md:1",
        "evaluate:docs/a.md:1",
        "evaluate:docs/b.md:1",
    }
    assert tracker.max_active["write"] == 2
    assert tracker.max_active["evaluation"] == 1


async def test_handler_failure_retries_once_then_succeeds() -> None:
    store = FakeTaskStore()
    await store.enqueue(
        run_id=RUN,
        task_id="write:docs/a.md:1",
        org_id="org-1",
        task_type="write",
        page_id="docs/a.md",
        artifact_version=1,
    )
    tracker = ConcurrencyTracker()
    executor = PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers={"write": _tracked_handler(tracker, "write", fail=lambda calls: calls == 1)},
        write_concurrency=2,
        evaluation_concurrency=2,
    )

    result = await executor.run(run_id=RUN, org_id="org-1")

    assert result.completed_task_ids == {"write:docs/a.md:1"}
    assert result.failed_task_ids == set()
    assert tracker.handler_calls["write:docs/a.md:1"] == 2
    task = store.claim_for("write:docs/a.md:1")
    assert task["status"] == "completed"
    assert task["infrastructure_retries"] == 1


async def test_second_infrastructure_failure_fails_task_and_terminates_run() -> None:
    store = FakeTaskStore()
    await _seed_page_run(store, "docs/a.md")
    tracker = ConcurrencyTracker()
    executor = PageWorkflowExecutor(
        repository=store,  # type: ignore[arg-type]
        handlers={"write": _tracked_handler(tracker, "write", fail=lambda calls: True)},
        write_concurrency=2,
        evaluation_concurrency=2,
    )

    result = await executor.run(run_id=RUN, org_id="org-1")

    assert result.failed_task_ids == {"write:docs/a.md:1"}
    assert tracker.handler_calls == {"write:docs/a.md:1": 2}
    dependent = store.claim_for("evaluate:docs/a.md:1")
    assert dependent["status"] == "pending"
    assert "evaluate:docs/a.md:1" not in tracker.handler_calls


async def test_expired_lease_resumes_from_another_owner() -> None:
    store = FakeTaskStore()
    await store.enqueue(
        run_id=RUN,
        task_id="write:docs/a.md:1",
        org_id="org-1",
        task_type="write",
        page_id="docs/a.md",
        artifact_version=1,
    )
    await store.claim_ready_tasks(run_id=RUN, lease_owner="dead-worker")
    dead = store.claim_for("write:docs/a.md:1")
    dead["lease_expires_at"] = datetime(2000, 1, 1, tzinfo=UTC)
    tracker = ConcurrencyTracker()
    executor = _executor(store, tracker, write_concurrency=2, evaluation_concurrency=2)

    result = await executor.run(run_id=RUN, org_id="org-1")

    assert result.completed_task_ids == {"write:docs/a.md:1"}
    completed = store.claim_for("write:docs/a.md:1")
    assert completed["status"] == "completed"
    assert completed["lease_owner"] == "page-workflow-executor"


async def test_completed_tasks_are_not_repeated_on_rerun() -> None:
    store = FakeTaskStore()
    await _seed_page_run(store, "docs/a.md")
    await _seed_page_run(store, "docs/b.md")
    tracker = ConcurrencyTracker()
    executor = _executor(store, tracker, write_concurrency=2, evaluation_concurrency=2)

    first = await executor.run(run_id=RUN, org_id="org-1")
    assert first.completed_task_ids == {
        "write:docs/a.md:1",
        "write:docs/b.md:1",
        "evaluate:docs/a.md:1",
        "evaluate:docs/b.md:1",
    }
    calls_after_first = dict(tracker.handler_calls)

    second = await executor.run(run_id=RUN, org_id="org-1")

    assert second.completed_task_ids == set()
    assert second.failed_task_ids == set()
    assert tracker.handler_calls == calls_after_first


async def test_unsatisfied_dependency_raises_deadlock_with_blocked_ids() -> None:
    store = FakeTaskStore()
    await _seed_page_run(store, "docs/a.md")
    store.claim_for("write:docs/a.md:1")["status"] = "failed"
    tracker = ConcurrencyTracker()
    executor = _executor(store, tracker, write_concurrency=2, evaluation_concurrency=2)

    with pytest.raises(DeadlockedWorkflowError) as exc:
        await executor.run(run_id=RUN, org_id="org-1")

    assert exc.value.blocked_task_ids == ["evaluate:docs/a.md:1"]


async def test_unregistered_task_type_raises_value_error() -> None:
    store = FakeTaskStore()
    await store.enqueue(
        run_id=RUN,
        task_id="speak:docs/a.md:1",
        org_id="org-1",
        task_type="speak",
        page_id="docs/a.md",
        artifact_version=1,
    )
    tracker = ConcurrencyTracker()
    executor = _executor(store, tracker, write_concurrency=1, evaluation_concurrency=1)

    with pytest.raises(ValueError, match="no handler registered"):
        await executor.run(run_id=RUN, org_id="org-1")


async def test_empty_run_returns_empty_result() -> None:
    store = FakeTaskStore()
    tracker = ConcurrencyTracker()
    executor = _executor(store, tracker, write_concurrency=1, evaluation_concurrency=1)

    result = await executor.run(run_id=RUN, org_id="org-1")

    assert isinstance(result, ExecutorResult)
    assert result.completed_task_ids == set()
    assert result.failed_task_ids == set()
    assert result.deadlocked_task_ids == set()
