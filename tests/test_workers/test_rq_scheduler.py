"""Regression: cron-scheduled RQ jobs must also dispatch by NAME, not serialize
closures (same root cause as enqueue_job)."""

from __future__ import annotations

from draftly.app.composition.rq_scheduler import setup_rq_scheduler
from draftly.app.composition.workers import SCHEDULED_JOBS
from draftly.app.workers.rq_dispatch import dispatch, register_handlers


class _FakeScheduler:
    def __init__(self) -> None:
        self.cron_calls: list[dict] = []

    def cron(self, schedule, func, kwargs, queue_name, id):
        self.cron_calls.append(
            {"schedule": schedule, "func": func, "kwargs": kwargs, "queue_name": queue_name, "id": id}
        )


def setup_function() -> None:
    register_handlers({})


def test_scheduler_schedules_importable_dispatch_with_task_name():
    from rq.utils import import_attribute

    fake = _FakeScheduler()
    handlers = {job["name"]: (lambda **kw: None) for job in SCHEDULED_JOBS}

    setup_rq_scheduler(scheduler=fake, task_handlers=handlers)

    assert len(fake.cron_calls) == len(SCHEDULED_JOBS)
    for record in fake.cron_calls:
        assert record["func"] is dispatch
        # kwargs must carry the task name so the worker can resolve its handler.
        assert "name" in record["kwargs"]
        assert record["kwargs"]["name"] in {j["name"] for j in SCHEDULED_JOBS}


def test_scheduler_skips_task_without_handler():
    fake = _FakeScheduler()
    setup_rq_scheduler(scheduler=fake, task_handlers={})
    assert fake.cron_calls == []
