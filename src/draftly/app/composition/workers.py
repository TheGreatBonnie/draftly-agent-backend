from __future__ import annotations

from typing import Any

import structlog

from draftly.app.composition.rq_jobs import build_rq_queues
from draftly.app.composition.workflows import ComposedWorkflows
from draftly.app.dependencies import ApplicationDependencies
from draftly.app.workers.task_runner import TaskRunner

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
