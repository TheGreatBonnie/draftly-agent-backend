from __future__ import annotations

from typing import Any

import structlog

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
