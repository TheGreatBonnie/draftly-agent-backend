"""RQ worker entrypoint.

Runs a single RQ worker consuming from all three queues
(scheduled, webhooks, default). Replaces workflow_worker.py,
indexing_worker.py, and evaluation_worker.py.

Usage:
    python -m workers.rq_worker
"""

from __future__ import annotations

import signal
from typing import Any

import structlog
from rq import SimpleWorker

from draftly.app.composition.rq_jobs import build_rq_queues
from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application
from draftly.app.services.init_lock import release_init_lock_sync
from draftly.app.workers.rq_dispatch import register_handlers, run_on_loop
from draftly.observability.logging import configure_logging

ONBOARDING_INIT_TASK = "onboarding.initialize"


class InitLockAwareWorker(SimpleWorker):
    """SimpleWorker that releases the per-org init lock after an
    ``onboarding.initialize`` job finishes (success or failure).

    The API process holds the lock from enqueue until completion so a
    mid-run refresh cannot start a duplicate pipeline (Task 1). Without a
    worker-side release the lock would persist for the full 7200s TTL after
    a successful run, blocking re-initialization for up to two hours.
    """

    def perform_job(self, job: Any, queue: Any) -> bool:
        try:
            return super().perform_job(job, queue)
        finally:
            self._release_onboarding_lock(job)

    def _release_onboarding_lock(self, job: Any) -> None:
        if not job or (job.meta or {}).get("task_name") != ONBOARDING_INIT_TASK:
            return
        kwargs = dict(job.kwargs or {})
        org_id = kwargs.get("org_id")
        run_id = kwargs.get("run_id")
        if not org_id or not run_id:
            return
        release_init_lock_sync(self.connection, org_id, run_id)
        structlog.get_logger(__name__).info(
            "onboarding_init_lock_released org=%s run_id=%s", org_id, run_id
        )


def main() -> None:
    settings = get_settings()
    configure_logging(settings=settings)
    structlog.contextvars.bind_contextvars(worker="rq")

    log = structlog.get_logger("draftly.worker.rq")

    application = create_application(settings=settings)

    # application.startup() is async: build the runtime (including
    # application.task_handlers = task_runner._tasks) BEFORE entering the blocking
    # RQ worker loop. It MUST run on the dispatcher's persistent event loop (not a
    # throwaway loop): the asyncpg/DB pool created here is bound to that loop, and
    # dispatch() later executes every job on the same loop (Bug 3).
    run_on_loop(application.startup())
    register_handlers(application.task_handlers or {})
    log.info("rq_handlers_registered count=%d", len(application.task_handlers or {}))

    import redis as sync_redis

    redis_url = settings.redis_url
    # RQ always zlib-compresses job data and needs raw bytes to decompress it;
    # decode_responses=True would UTF-8-decode the compressed payload and crash.
    conn = sync_redis.Redis.from_url(redis_url, decode_responses=False)

    queues = build_rq_queues(conn, prefix=settings.rq_queue_prefix)

    queue_names = [f"{settings.rq_queue_prefix}:{q}" for q in queues]

    worker = InitLockAwareWorker(
        queue_names,
        connection=conn,
        serializer="json",
    )

    log.info(
        "RQ worker starting",
        queues=queue_names,
        prefix=settings.rq_queue_prefix,
    )

    # RQ's BaseWorker.request_stop(signum, frame) IS a signal handler (it
    # requests a warm shutdown, then re-arms for a force stop on a second
    # signal). Register it directly rather than wrapping it — calling it with
    # no args raises TypeError (it expects signum + frame).
    def shutdown(signum: int, frame: object) -> None:
        log.info("RQ worker shutting down", signal=signum)
        worker.request_stop(signum, frame)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        worker.work()
    except KeyboardInterrupt:
        log.info("RQ worker interrupted")
    except Exception:
        log.exception("RQ worker failed")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
