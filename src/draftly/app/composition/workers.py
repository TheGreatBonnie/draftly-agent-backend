from __future__ import annotations

from typing import Any

import structlog

from draftly.app.composition.rq_jobs import build_rq_queues
from draftly.app.composition.workflows import ComposedWorkflows
from draftly.app.dependencies import ApplicationDependencies
from draftly.app.workers.scheduler import DraftlyScheduler
from draftly.app.workers.scheduler_adapter import SchedulerClientAdapter
from draftly.app.workers.task_runner import TaskRunner
from draftly.app.workers.worker import DraftlyWorker

logger = structlog.get_logger(__name__)


# Scheduled task name → WorkflowRegistry workflow name. Surface workflows
# (github_pr / github_issue / slack_support / discord_support) are NOT
# here: they run through the webhook → WorkflowRunner path (§7.4).
TASK_REGISTRY: dict[str, str] = {
    "documentation.sync": "documentation_sync",
    "documentation.sync_repository": "documentation_sync",
    "documentation.stale_scan": "documentation_audit",
    "support.gap_scan": "feedback_loop",
    "evaluation.loop": "evaluation_loop",
    "onboarding.initialize": "onboarding_initialize",
    "memory.curation": "memory_curation",
    "memory.maintenance": "memory_maintenance",
}


def _wrap_workflow(
    workflow_func: Any,
    context: Any,
) -> Any:
    """Wrap a workflow function to run against the composed context."""

    async def handler(**kwargs: Any) -> Any:
        return await workflow_func(context, **kwargs)

    return handler


def build_task_runner(
    *,
    workflows: ComposedWorkflows,
    dependencies: ApplicationDependencies,
) -> TaskRunner:
    """
    Build a TaskRunner with all scheduled workflows registered as tasks.

    Each workflow function is bound to the composed ``WorkflowContext``
    so handlers only receive job arguments.
    """
    del dependencies  # context already carries the repositories

    runner = TaskRunner()
    registry = workflows.registry

    for task_name, workflow_name in TASK_REGISTRY.items():
        workflow_func = registry.get(workflow_name)
        if workflow_func is None:
            raise ValueError(f"task {task_name} has no workflow {workflow_name}")
        runner.register(task_name, _wrap_workflow(workflow_func, workflows.context))
        logger.debug("registered task task_name=%s", task_name)

    logger.info(
        "task runner built tasks=%d",
        len(TASK_REGISTRY),
    )

    return runner


SCHEDULED_JOBS: list[dict[str, Any]] = [
    {
        "id": "documentation-sync",
        "name": "documentation.sync",
        "schedule": "0 2 * * *",
        "arguments": {},
    },
    {
        "id": "stale-docs-scan",
        "name": "documentation.stale_scan",
        "schedule": "0 3 * * 0",
        "arguments": {},
    },
    {
        "id": "support-gap-scan",
        "name": "support.gap_scan",
        "schedule": "0 4 * * *",
        "arguments": {},
    },
    {
        "id": "evaluation-loop",
        "name": "evaluation.loop",
        "schedule": "0 5 * * *",
        "arguments": {},
    },
    {
        "id": "memory-curation",
        "name": "memory.curation",
        "schedule": "*/30 * * * *",
        "arguments": {},
    },
    {
        "id": "memory-maintenance",
        "name": "memory.maintenance",
        "schedule": "0 6 * * 0",
        "arguments": {},
    },
]


def build_scheduler_client(
    *,
    task_runner: TaskRunner,
) -> Any:
    """
    Placeholder for the scheduled-job integration.

    Phase 6 replaces the removed scheduler integration with Strands
    session/graph scheduling. Kept as a stub so ``build_worker``'s
    signature stays stable.
    """

    del task_runner

    return None


def build_rq_worker(
    *,
    task_runner: TaskRunner,
    redis_url: str,
    prefix: str = "draftly",
) -> dict[str, Any]:
    """Build RQ infrastructure: queues, scheduler config, task handlers.

    Returns a dict with:
        - rq_queues: dict of queue_name → Queue
        - task_handlers: dict of task_name → async handler
        - rq_scheduler: Scheduler instance (or None if disabled)
        - rq_connection: sync Redis connection
    """
    import redis as sync_redis
    from rq_scheduler import Scheduler

    conn = sync_redis.Redis.from_url(redis_url, decode_responses=True)
    queues = build_rq_queues(conn, prefix=prefix)

    task_handlers = {}
    for task_name in TASK_REGISTRY:
        if task_runner.has_task(task_name):
            handler = task_runner._tasks.get(task_name)
            if handler is not None:
                task_handlers[task_name] = handler

    scheduler = Scheduler(connection=conn)

    logger.info(
        "rq worker built queues=%d tasks=%d",
        len(queues),
        len(task_handlers),
    )

    return {
        "rq_queues": queues,
        "task_handlers": task_handlers,
        "rq_scheduler": scheduler,
        "rq_connection": conn,
    }


def build_worker(
    *,
    task_runner: TaskRunner,
    scheduler_client: Any,
    interval_seconds: int = 30,
) -> DraftlyWorker:
    """
    Build the complete DraftlyWorker with its scheduler loop.

    The scheduler polls the job registry (via SchedulerClientAdapter)
    for due jobs and dispatches them through the task runner.
    """
    adapter = SchedulerClientAdapter(scheduler_client)

    scheduler = DraftlyScheduler(
        registry=adapter,
        task_runner=task_runner,
        interval_seconds=interval_seconds,
    )

    worker = DraftlyWorker(
        task_runner=task_runner,
        scheduler=scheduler,
    )

    logger.info("worker built with scheduler_interval=%ss", interval_seconds)

    return worker
