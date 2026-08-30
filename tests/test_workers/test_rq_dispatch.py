"""Regression: RQ jobs must dispatch by TASK NAME, not by serializing closures.

RQ re-imports a job's function by qualified path.  The old enqueue path wrapped
handlers in a ``make_sync_handler`` LOCAL closure that is neither importable by
path nor picklable, so the worker could never execute a job
(``ValueError: Invalid attribute name``).  The fix enqueues a module-level,
importable dispatch() that resolves the handler by task name against the
worker's own in-process handler registry.
"""

from __future__ import annotations

import asyncio

import fakeredis

from draftly.app.composition.rq_jobs import build_rq_queues, enqueue_job
from draftly.app.workers.rq_dispatch import (
    dispatch,
    register_handlers,
)

TASK = "onboarding.initialize"


async def _fake_onboarding(org_id: str = "", run_id: str = "", **kw: object) -> dict:
    return {"status": "executed", "org_id": org_id, "run_id": run_id}


def _raw_conn() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=False)


def setup_function() -> None:
    register_handlers({})


def test_dispatch_runs_registered_async_handler():
    register_handlers({TASK: _fake_onboarding})

    result = dispatch(name=TASK, org_id="org-1", run_id="run-1")

    assert result == {"status": "executed", "org_id": "org-1", "run_id": "run-1"}


def test_dispatch_reuses_a_persistent_loop_across_calls():
    """Regression for Bug 3: dispatch must run handlers on ONE persistent loop.

    The workflow holds asyncpg/DB connections that are bound to the loop they
    were created on.  If dispatch() spun up and closed a fresh loop per job
    (as it did after we ran application.startup() on a separate loop and then
    closed it), the connections die with the loop and the workflow crashes
    with "Event loop is closed" / "connection was closed in the middle of
    operation".  dispatch must reuse the same live loop across all calls.
    """
    loops = []

    async def capture(**kw: object) -> dict:
        loops.append(asyncio.get_running_loop())
        return {}

    register_handlers({TASK: capture})

    dispatch(name=TASK)
    dispatch(name=TASK)

    assert len(loops) == 2
    assert loops[0] is loops[1]


def test_dispatch_missing_handler_raises():
    register_handlers({})
    try:
        dispatch(name="does.not.exist")
    except ValueError as exc:
        assert "Unknown Draftly task" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_enqueue_job_serializes_module_level_dispatch_not_closure():
    """The enqueued func must be importable by RQ (never a make_sync_handler closure)."""
    register_handlers({TASK: _fake_onboarding})
    queues = build_rq_queues(_raw_conn())

    job = enqueue_job(
        queues=queues,
        task_handlers={TASK: _fake_onboarding},
        task_name=TASK,
        org_id="org-1",
        run_id="run-9",
    )

    # RQ resolves the job's function from the stored reference; a closure path
    # would be "<locals>.sync_handler" and fail re-import.
    assert "make_sync_handler" not in job.func_name
    # The stored reference re-imports to the module-level dispatch callable.
    from rq.utils import import_attribute

    resolved = import_attribute(job.func_name)
    from draftly.app.workers.rq_dispatch import dispatch as real_dispatch

    assert resolved is real_dispatch
    assert job.kwargs.get("name") == TASK
    assert job.kwargs.get("org_id") == "org-1"


def test_enqueued_job_executes_through_dispatch():
    register_handlers({TASK: _fake_onboarding})
    queues = build_rq_queues(_raw_conn())

    job = enqueue_job(
        queues=queues,
        task_handlers={TASK: _fake_onboarding},
        task_name=TASK,
        org_id="org-1",
        run_id="run-7",
    )

    # Re-fetch as the worker does, then execute via the importable dispatcher.
    fetched = queues["default"].fetch_job(job.id)
    result = dispatch(**fetched.kwargs)
    assert result == {"status": "executed", "org_id": "org-1", "run_id": "run-7"}


def test_enqueued_job_timeout_exceeds_workflow_watchdog():
    """Regression: RQ's 180s DEFAULT_TIMEOUT killed onboarding.initialize long
    before the workflow's own 1200s watchdog (INIT_WORKFLOW_TIMEOUT_SECONDS)
    could fire. The job must carry an explicit timeout greater than the
    workflow watchdog so the workflow logic — not RQ's default — bounds the run.
    """
    from draftly.workflows.onboarding.initialize import (
        INIT_WORKFLOW_TIMEOUT_SECONDS,
    )

    register_handlers({TASK: _fake_onboarding})
    queues = build_rq_queues(_raw_conn())

    job = enqueue_job(
        queues=queues,
        task_handlers={TASK: _fake_onboarding},
        task_name=TASK,
        org_id="org-1",
        run_id="run-timeout",
    )

    assert job.timeout is not None
    assert job.timeout > INIT_WORKFLOW_TIMEOUT_SECONDS


def test_enqueued_job_timeout_for_short_tasks_stays_bounded():
    """Short/scheduled tasks must not inherit the long-running onboarding
    timeout; they keep a modest explicit timeout (bounded, not the long one)."""
    short_task = "documentation.sync"
    register_handlers({short_task: _fake_onboarding})
    queues = build_rq_queues(_raw_conn())

    job = enqueue_job(
        queues=queues,
        task_handlers={short_task: _fake_onboarding},
        task_name=short_task,
        org_id="org-1",
    )

    assert job.timeout is not None
    assert job.timeout <= 600
