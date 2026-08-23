from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ScheduledJob:
    """Minimal scheduled-job record (scheduler integration was removed)."""

    id: str
    name: str
    schedule: str
    handler: Callable[..., Any]
    next_run_at: datetime | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class DueJob:
    """Adapted job ready for execution by DraftlyScheduler."""

    name: str
    handler: Callable[..., Any]
    arguments: dict[str, Any]


class SchedulerClientAdapter:
    """
    Adapt a job registry to the interface expected by DraftlyScheduler
    (get_due_jobs).

    Computes next_run_at from cron schedules using croniter if available,
    otherwise relies on caller-provided next_run_at.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_due_jobs(self) -> list[DueJob]:
        now = datetime.now(UTC)
        due: list[DueJob] = []

        for job in self._client.jobs.values():
            if job.next_run_at is None:
                continue

            if job.next_run_at <= now:
                logger.info(
                    "scheduler job due job_id=%s name=%s",
                    job.id,
                    job.name,
                )
                due.append(
                    DueJob(
                        name=job.name,
                        handler=job.handler,
                        arguments={},
                    )
                )
                self._advance_job(job)

        return due

    def _advance_job(self, job: ScheduledJob) -> None:
        """Compute next_run_at after execution (or at registration if croniter available)."""
        try:
            from croniter import croniter  # type: ignore[import-untyped]

            it = croniter(job.schedule, job.next_run_at or datetime.now(UTC))
            job.next_run_at = it.get_next(datetime)
            logger.debug(
                "scheduler advanced job_id=%s next_run_at=%s",
                job.id,
                job.next_run_at.isoformat(),
            )
        except ImportError:
            logger.debug(
                "croniter not available; skipping next_run_at update for job_id=%s",
                job.id,
            )
