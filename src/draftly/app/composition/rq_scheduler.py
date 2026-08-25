"""rq-scheduler setup for cron-triggered jobs.

Replaces the custom DraftlyScheduler polling loop with
rq-scheduler's built-in cron scheduling.
"""

from __future__ import annotations

from typing import Any

import structlog
from rq_scheduler import Scheduler

from draftly.app.composition.workers import SCHEDULED_JOBS
from draftly.app.workers.async_sync import make_sync_handler

logger = structlog.get_logger(__name__)


def setup_rq_scheduler(
    scheduler: Scheduler,
    task_handlers: dict[str, Any],
    prefix: str = "draftly",
) -> None:
    """Register all cron jobs with rq-scheduler.

    Args:
        scheduler: An rq-scheduler Scheduler instance.
        task_handlers: Dict of task_name → async handler function.
        prefix: Redis key prefix for queue names.
    """
    for job_def in SCHEDULED_JOBS:
        task_name = job_def["name"]
        handler = task_handlers.get(task_name)

        if handler is None:
            logger.warning(
                "Skipping scheduled job — no handler",
                job_id=job_def["id"],
                task=task_name,
            )
            continue

        sync_handler = make_sync_handler(handler)

        scheduler.cron(
            job_def["schedule"],
            func=sync_handler,
            kwargs=job_def.get("arguments", {}),
            queue_name=f"{prefix}:scheduled",
            id=job_def["id"],
        )

        logger.info(
            "Scheduled job registered",
            job_id=job_def["id"],
            task=task_name,
            cron=job_def["schedule"],
        )

    logger.info(
        "rq-scheduler setup complete",
        jobs=len(SCHEDULED_JOBS),
    )
