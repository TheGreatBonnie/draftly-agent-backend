"""RQ worker entrypoint.

Runs a single RQ worker consuming from all three queues
(scheduled, webhooks, default). Replaces workflow_worker.py,
indexing_worker.py, and evaluation_worker.py.

Usage:
    python -m workers.rq_worker
"""

from __future__ import annotations

import signal
import sys

import structlog
from rq import SimpleWorker

from draftly.app.config import get_settings
from draftly.app.composition.rq_jobs import build_rq_queues
from draftly.app.lifecycle import create_application
from draftly.observability.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings=settings)
    structlog.contextvars.bind_contextvars(worker="rq")

    log = structlog.get_logger("draftly.worker.rq")

    application = create_application(settings=settings)

    import redis as sync_redis

    redis_url = settings.redis_url
    conn = sync_redis.Redis.from_url(redis_url, decode_responses=True)

    queues = build_rq_queues(conn, prefix=settings.rq_queue_prefix)

    queue_names = [f"{settings.rq_queue_prefix}:{q}" for q in queues]

    worker = SimpleWorker(
        queue_names,
        connection=conn,
        serializer="json",
    )

    log.info(
        "RQ worker starting",
        queues=queue_names,
        prefix=settings.rq_queue_prefix,
    )

    def shutdown(signum: int, frame: object) -> None:
        log.info("RQ worker shutting down", signal=signum)
        worker.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        application.startup()
        worker.work()
    except KeyboardInterrupt:
        log.info("RQ worker interrupted")
    except Exception:
        log.exception("RQ worker failed")
    finally:
        worker.stop()
        conn.close()


if __name__ == "__main__":
    main()
