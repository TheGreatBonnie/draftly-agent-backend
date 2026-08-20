from __future__ import annotations

import logging
from typing import Any

from app.composition.workflows import WorkflowRegistry
from app.dependencies import ApplicationDependencies
from app.workers.scheduler import DraftlyScheduler
from app.workers.scheduler_adapter import SchedulerClientAdapter
from app.workers.task_runner import TaskRunner
from app.workers.worker import DraftlyWorker
from integrations.scheduler.client import SchedulerClient

logger = logging.getLogger(__name__)


TASK_REGISTRY: dict[str, str] = {
    "documentation.sync": "documentation_sync",
    "documentation.generation": "documentation_generation",
    "documentation.review": "documentation_review",
    "documentation.health_check": "documentation_health_check",
    "github.issue": "issue",
    "github.pull_request": "pull_request",
    "github.release": "release",
    "github.repository_sync": "repository_sync",
    "support.github": "github_support",
    "support.slack": "slack_support",
    "support.discord": "discord_support",
    "support.escalation": "support_escalation",
    "evaluation.loop": "evaluation_loop",
    "evaluation.regression": "regression_loop",
    "evaluation.failure_recovery": "failure_recovery",
    "documentation.stale_scan": "stale_docs_scan",
    "support.gap_scan": "support_gap_scan",
    "github.release_sync": "release_sync",
    "review.expiry": "review_expiry",
}


def _wrap_workflow(
    workflow_func: Any,
    dependencies: ApplicationDependencies,
) -> Any:
    """Wrap a workflow function to create WorkflowContext on invocation."""
    async def handler(**kwargs: Any) -> Any:
        from workflows.context import WorkflowContext

        context = WorkflowContext(
            repositories=dependencies.repositories,
            memory=dependencies.memory,
            evaluation=dependencies.evaluation,
        )
        return await workflow_func(context, **kwargs)

    return handler


def build_task_runner(
    *,
    workflows: WorkflowRegistry,
    dependencies: ApplicationDependencies,
) -> TaskRunner:
    """
    Build a TaskRunner with all workflow functions registered as named tasks.

    Each workflow function is wrapped in a handler that constructs
    a WorkflowContext from the application dependencies.
    """
    runner = TaskRunner()

    for task_name, workflow_attr in TASK_REGISTRY.items():
        workflow_func = getattr(workflows, workflow_attr)
        runner.register(task_name, _wrap_workflow(workflow_func, dependencies))
        logger.debug("registered task task_name=%s", task_name)

    logger.info(
        "task runner built tasks=%d",
        len(TASK_REGISTRY),
    )

    return runner


SCHEDULED_JOBS: list[dict[str, Any]] = [
    {
        "id": "daily-health-check",
        "name": "documentation.health_check",
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
        "id": "release-sync",
        "name": "github.release_sync",
        "schedule": "0 1 * * *",
        "arguments": {},
    },
    {
        "id": "review-expiry",
        "name": "review.expiry",
        "schedule": "0 * * * *",
        "arguments": {},
    },
]


def build_scheduler_client(
    *,
    task_runner: TaskRunner,
) -> SchedulerClient:
    """Register scheduled jobs with cron expressions."""
    client = SchedulerClient()

    for job_config in SCHEDULED_JOBS:
        task_name = job_config["name"]
        task_args = job_config["arguments"]

        if not task_runner.has_task(task_name):
            raise ValueError(
                f"Scheduled job {job_config['id']!r} references unknown task "
                f"{task_name!r}; add it to TASK_REGISTRY or fix the job config"
            )

        def make_handler(name: str = task_name, args: dict = task_args):
            async def handler(**kwargs: Any) -> Any:
                return await task_runner.run(name, **args, **kwargs)
            return handler  # type: ignore[return-value]

        client.create(
            job_id=job_config["id"],
            name=task_name,
            schedule=job_config["schedule"],
            handler=make_handler(),
            arguments=task_args,
        )
        logger.debug(
            "registered scheduled job job_id=%s task=%s schedule=%s",
            job_config["id"],
            task_name,
            job_config["schedule"],
        )

    logger.info("scheduler client built jobs=%d", len(SCHEDULED_JOBS))

    return client


def build_worker(
    *,
    task_runner: TaskRunner,
    scheduler_client: SchedulerClient,
    interval_seconds: int = 30,
) -> DraftlyWorker:
    """
    Build the complete DraftlyWorker with its scheduler loop.

    The scheduler polls the SchedulerClient (via SchedulerClientAdapter)
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
