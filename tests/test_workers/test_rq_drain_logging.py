"""A drain must announce itself, so an idle tick reads differently from a busy one.

RQ logs ``Job OK`` on success and ``Job failed`` on failure, and nothing at all
for an empty queue. Before this, an idle drain and a productive drain emitted
the same pair of lines -- ``RQ worker work loop starting`` then
``done, quitting`` -- and could only be told apart by the elapsed time between
them (observed 2s idle vs 49s busy).

``drain_complete`` closes that gap with an explicit job count and the queue
depths left behind.

Imports are deferred inside each test because importing ``workers.rq_worker``
pulls in ``draftly.app.lifecycle`` and the settings model, which reads
DATABASE_URL and REDIS_URL.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def queue_counts(monkeypatch):
    """Replace rq.Queue with a fake exposing only .count, and seed its depths."""
    import workers.rq_worker as worker_module

    counts: dict[str, int] = {}

    class _FakeQueue:
        def __init__(self, name: str, connection=None, **kwargs) -> None:
            self.name = name

        @property
        def count(self) -> int:
            return counts.get(self.name, 0)

    monkeypatch.setattr(worker_module, "Queue", _FakeQueue)
    return counts


class _RecordingLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def info(self, event: str, **kw) -> None:
        self.records.append((event, kw))


class _FakeWorker:
    connection = object()


def test_idle_drain_reports_zero_jobs(queue_counts):
    """The whole point: an idle tick must be distinguishable from a busy one."""
    from workers.rq_worker import log_drain_complete

    queue_counts.update({"draftly:webhooks": 0, "draftly:default": 3})

    class _Worker(_FakeWorker):
        jobs_processed = 0

    log = _RecordingLog()
    log_drain_complete(
        _Worker(),
        queue_names=["draftly:webhooks", "draftly:default"],
        log=log,
        elapsed_s=1.84,
    )

    assert log.records == [
        (
            "drain_complete",
            {
                "jobs_processed": 0,
                "elapsed_ms": 1840,
                "queue_depths": {"draftly:webhooks": 0, "draftly:default": 3},
            },
        )
    ]


def test_busy_drain_reports_the_job_count(queue_counts):
    from workers.rq_worker import log_drain_complete

    class _Worker(_FakeWorker):
        jobs_processed = 1

    log = _RecordingLog()
    log_drain_complete(
        _Worker(),
        queue_names=["draftly:webhooks"],
        log=log,
        elapsed_s=49.38,
    )

    assert log.records == [
        (
            "drain_complete",
            {
                "jobs_processed": 1,
                "elapsed_ms": 49380,
                "queue_depths": {"draftly:webhooks": 0},
            },
        )
    ]


def test_missing_counter_reads_as_zero(queue_counts):
    """Defensive: a worker without the attribute must not crash the drain."""
    from workers.rq_worker import log_drain_complete

    log = _RecordingLog()
    log_drain_complete(_FakeWorker(), queue_names=[], log=log, elapsed_s=0.5)

    assert log.records[0][1]["jobs_processed"] == 0


def test_worker_counts_a_successful_job(queue_counts, monkeypatch):
    from workers.rq_worker import InitLockAwareWorker

    monkeypatch.setattr("rq.SimpleWorker.perform_job", lambda self, job, queue: True)
    monkeypatch.setattr(
        InitLockAwareWorker, "_release_onboarding_lock", lambda self, job: None
    )

    worker = InitLockAwareWorker.__new__(InitLockAwareWorker)
    worker.jobs_processed = 0

    assert worker.perform_job(object(), object()) is True
    assert worker.jobs_processed == 1


def test_worker_counts_a_raising_job(queue_counts, monkeypatch):
    """The count must live in ``finally``, or a drain of only failures reads idle."""
    from workers.rq_worker import InitLockAwareWorker

    def _boom(self, job, queue):
        raise RuntimeError("job blew up")

    monkeypatch.setattr("rq.SimpleWorker.perform_job", _boom)
    monkeypatch.setattr(
        InitLockAwareWorker, "_release_onboarding_lock", lambda self, job: None
    )

    worker = InitLockAwareWorker.__new__(InitLockAwareWorker)
    worker.jobs_processed = 0

    with pytest.raises(RuntimeError):
        worker.perform_job(object(), object())

    assert worker.jobs_processed == 1


def test_worker_initialises_the_counter():
    """``__init__`` must set it, so the first drain does not need getattr."""
    import inspect

    from workers.rq_worker import InitLockAwareWorker

    # Constructing a real SimpleWorker needs a live Redis connection, so assert
    # on the source of __init__ instead: it assigns the attribute.
    assert "self.jobs_processed = 0" in inspect.getsource(InitLockAwareWorker.__init__)


def test_run_worker_loop_emits_drain_complete_on_exit(queue_counts, monkeypatch):
    """The event must fire when the loop ends, not only when it succeeds."""
    from workers.rq_worker import run_worker_loop

    calls: list[dict] = []

    class _Worker(_FakeWorker):
        jobs_processed = 2

        def work(self, burst: bool) -> None:
            calls.append({"burst": burst})

    log = _RecordingLog()
    run_worker_loop(
        _Worker(), burst=True, log=log, queue_names=["draftly:webhooks"]
    )

    assert calls == [{"burst": True}]
    assert [event for event, _ in log.records] == ["drain_complete"]
    assert log.records[0][1]["jobs_processed"] == 2


def test_run_worker_loop_reports_failure_and_reraises(queue_counts):
    """A crash must still be observable, and must not be swallowed."""
    from workers.rq_worker import run_worker_loop

    class _Worker(_FakeWorker):
        jobs_processed = 0

        def work(self, burst: bool) -> None:
            raise RuntimeError("boom")

    class _ExplodingLog(_RecordingLog):
        def exception(self, event: str, **kw) -> None:
            self.records.append((event, kw))

    log = _ExplodingLog()
    with pytest.raises(RuntimeError):
        run_worker_loop(_Worker(), burst=True, log=log, queue_names=[])

    events = [event for event, _ in log.records]
    assert "RQ worker failed" in events
    assert "drain_complete" in events