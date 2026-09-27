"""Durable bounded DAG executor for the page workflow.

Runs one run's task graph against a ``PageWorkflowRepository``: leases funnel
through the repository so tasks survive worker restarts, concurrency is bounded
by separate semaphores for write and evaluation handlers, and every outcome is
persisted before the next claim. A handler raising is treated as an
infrastructure failure (retried once by ``retry_or_fail_task``); the second
failure fails the task and terminates the run because its dependents can never
claim. Quality failures are ordinary handler results that schedule revision
tasks, not exceptions, so they never consume the infrastructure retry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import structlog

from draftly.agents.documentation.repo_read_cache import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    RepoReadCache,
    reset_repo_read_cache,
    set_repo_read_cache,
)
from draftly.orchestration.page_workflow.repository import (
    PageWorkflowRepository,
    WorkflowTask,
)

TaskHandler = Callable[[WorkflowTask], Awaitable[dict[str, Any]]]
logger = structlog.get_logger(__name__)


class DeadlockedWorkflowError(Exception):
    """No task is claimable while pending tasks remain.

    Every ``pending`` task names a dependency that is failed or cancelled (or
    the task graph is cyclic), so the run can never make progress.
    ``blocked_task_ids`` lists those un-claimable pending task IDs.
    """

    def __init__(self, message: str, *, blocked_task_ids: list[str]) -> None:
        super().__init__(message)
        self.blocked_task_ids = blocked_task_ids


@dataclass(frozen=True)
class ExecutorResult:
    """Aggregate outcome of one ``PageWorkflowExecutor.run`` invocation."""

    completed_task_ids: set[str] = field(default_factory=set)
    failed_task_ids: set[str] = field(default_factory=set)
    deadlocked_task_ids: set[str] = field(default_factory=set)


class PageWorkflowExecutor:
    """Schedules one run's workflow tasks with durable leases and retries.

    ``write`` handlers share a write-semaphore and every other task type
    (``evaluate``, ``review``) shares the evaluation-semaphore, so distinct
    pools bound concurrency independently.
    """

    def __init__(
        self,
        *,
        repository: PageWorkflowRepository,
        handlers: dict[str, TaskHandler],
        write_concurrency: int,
        evaluation_concurrency: int,
        lease_seconds: int = 300,
        lease_owner: str = "page-workflow-executor",
        read_cache_max_entries: int = DEFAULT_MAX_ENTRIES,
        read_cache_max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if write_concurrency < 1:
            raise ValueError("write_concurrency must be >= 1")
        if evaluation_concurrency < 1:
            raise ValueError("evaluation_concurrency must be >= 1")
        if not handlers:
            raise ValueError("handlers must not be empty")
        self.repository = repository
        self.handlers = handlers
        self.write_concurrency = write_concurrency
        self.evaluation_concurrency = evaluation_concurrency
        self.lease_seconds = lease_seconds
        self.lease_owner = lease_owner
        self.read_cache_max_entries = read_cache_max_entries
        self.read_cache_max_bytes = read_cache_max_bytes

    async def run(self, run_id: str, org_id: str) -> ExecutorResult:
        """Execute every claimable task of ``run_id`` and return the outcome.

        ``org_id`` is accepted for interface symmetry with the rest of the
        workflow; task persistence is scoped by ``run_id``.

        One run-scoped :class:`RepoReadCache` is installed here, before any task
        is spawned, and torn down afterwards. ``asyncio`` copies a context at
        task creation, so every writer spawned by ``_drive`` inherits this
        binding and they share a single cache object - that sharing is the
        entire point, and it is why the installation lives here rather than in
        a handler. ``_drive`` is awaited directly rather than scheduled, so the
        binding is this coroutine's own and the ``finally`` restores it.
        """
        cache = RepoReadCache(
            max_entries=self.read_cache_max_entries,
            max_bytes=self.read_cache_max_bytes,
        )
        token = set_repo_read_cache(cache)
        try:
            result = await self._drive(run_id, org_id)
        finally:
            reset_repo_read_cache(token)
        logger.info("page_workflow_read_cache", run_id=run_id, **cache.stats())
        return result

    async def _drive(self, run_id: str, org_id: str) -> ExecutorResult:
        """The scheduling loop behind :meth:`run`."""
        write_semaphore = asyncio.Semaphore(self.write_concurrency)
        evaluation_semaphore = asyncio.Semaphore(self.evaluation_concurrency)
        completed: set[str] = set()
        failed: set[str] = set()

        while True:
            await self.repository.reset_expired_leases(run_id=run_id)
            claimed = await self.repository.claim_ready_tasks(
                run_id=run_id,
                lease_owner=self.lease_owner,
                lease_seconds=self.lease_seconds,
            )
            for task in claimed:
                if task.task_type not in self.handlers:
                    raise ValueError(
                        f"no handler registered for workflow task type {task.task_type!r}"
                    )

            if claimed:
                outcomes = await asyncio.gather(
                    *(
                        self._execute(task, write_semaphore, evaluation_semaphore)
                        for task in claimed
                    ),
                    return_exceptions=True,
                )
                for task, outcome in zip(claimed, outcomes):
                    if isinstance(outcome, Exception):
                        status = await self.repository.retry_or_fail_task(
                            run_id=run_id,
                            task_id=task.task_id,
                            owner=self.lease_owner,
                            error=str(outcome) or type(outcome).__name__,
                        )
                        logger.error(
                            "page_workflow_task_error",
                            run_id=run_id,
                            task_id=task.task_id,
                            task_type=task.task_type,
                            page_id=task.page_id,
                            artifact_version=task.artifact_version,
                            status=status,
                            error_type=type(outcome).__name__,
                            error=str(outcome) or type(outcome).__name__,
                        )
                        if status == "failed":
                            failed.add(task.task_id)
                    else:
                        await self.repository.complete_task(
                            run_id=run_id,
                            task_id=task.task_id,
                            owner=self.lease_owner,
                            output_data=cast(dict[str, Any], outcome),
                        )
                        completed.add(task.task_id)
                if failed:
                    return ExecutorResult(
                        completed_task_ids=completed,
                        failed_task_ids=failed,
                    )
                continue

            snapshot = await self.repository.get_tasks(run_id=run_id)
            pending = [task for task in snapshot if task.status == "pending"]
            running = [task for task in snapshot if task.status == "running"]
            if pending and not running:
                raise DeadlockedWorkflowError(
                    "workflow is deadlocked: pending tasks can never become claimable",
                    blocked_task_ids=sorted(task.task_id for task in pending),
                )
            if pending or running:
                continue
            return ExecutorResult(
                completed_task_ids=completed,
                failed_task_ids=failed,
            )

    async def _execute(
        self,
        task: WorkflowTask,
        write_semaphore: asyncio.Semaphore,
        evaluation_semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        """Run the task's handler under the semaphore for its type pool."""
        semaphore = write_semaphore if task.task_type == "write" else evaluation_semaphore
        async with semaphore:
            return await self.handlers[task.task_type](task)
