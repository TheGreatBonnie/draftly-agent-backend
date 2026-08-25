"""RQ job registry and enqueue functions.

Maps task names to RQ queues and provides the enqueue interface
used by API endpoints and rq-scheduler.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from rq import Queue, Retry
from rq.job import Job

from draftly.app.composition.workers import TASK_REGISTRY
from draftly.app.workers.async_sync import make_sync_handler

logger = structlog.get_logger(__name__)


# Task name → Queue name mapping
QUEUE_MAP: dict[str, str] = {
    "documentation.sync": "scheduled",
    "documentation.sync_repository": "scheduled",
    "documentation.stale_scan": "scheduled",
    "support.gap_scan": "scheduled",
    "evaluation.loop": "scheduled",
    "memory.curation": "scheduled",
    "memory.maintenance": "scheduled",
    "onboarding.initialize": "default",
    "github_pr": "webhooks",
    "github_release": "webhooks",
    "github_issue": "webhooks",
    "slack_support": "webhooks",
    "discord_support": "webhooks",
}


def get_queue_for_task(task_name: str) -> str:
    """Return the RQ queue name for a given task."""
    return QUEUE_MAP.get(task_name, "default")


def build_rq_queues(
    connection: Any,
    prefix: str = "draftly",
) -> dict[str, Queue]:
    """Build RQ queue instances for all configured queues."""
    queues = {}
    for queue_name in ("scheduled", "webhooks", "default"):
        queues[queue_name] = Queue(
            f"{prefix}:{queue_name}",
            connection=connection,
        )
    return queues


def enqueue_job(
    queues: dict[str, Queue],
    task_handlers: dict[str, Any],
    task_name: str,
    prefix: str = "draftly",
    **kwargs: Any,
) -> Job:
    """Enqueue a task to the appropriate RQ queue.

    Args:
        queues: Dict of queue_name → Queue from build_rq_queues().
        task_handlers: Dict of task_name → async handler function.
        task_name: The task to enqueue (must be in TASK_REGISTRY).
        prefix: Redis key prefix for queue names.
        **kwargs: Arguments passed to the workflow handler.

    Returns:
        The RQ Job object.

    Raises:
        ValueError: If task_name is not registered.
    """
    if task_name not in TASK_REGISTRY:
        raise ValueError(f"Unknown task: {task_name}")

    queue_name = get_queue_for_task(task_name)
    queue = queues[queue_name]

    handler = task_handlers.get(task_name)
    if handler is None:
        raise ValueError(f"No handler registered for task: {task_name}")

    sync_handler = make_sync_handler(handler)
    job_id = str(uuid.uuid4())

    job = queue.enqueue(
        sync_handler,
        kwargs=kwargs,
        job_id=job_id,
        retry=Retry(max=3, interval=[10, 30, 60]),
        ttl=3600,
        meta={
            "task_name": task_name,
            "enqueued_at": datetime.now(UTC).isoformat(),
        },
    )

    logger.info(
        "Job enqueued",
        task=task_name,
        queue=queue_name,
        job_id=job_id,
    )

    return job
