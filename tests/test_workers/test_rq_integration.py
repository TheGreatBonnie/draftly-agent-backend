"""Integration tests for RQ job enqueue and execution cycle."""

from __future__ import annotations

import fakeredis
from rq import Queue
from rq.job import Job

from draftly.app.services.init_lock import init_lock_key
from draftly.app.workers.async_sync import make_sync_handler


async def _mock_workflow(org_id: str = "test-org") -> dict:
    return {"status": "ok", "org_id": org_id}


class TestRQIntegration:
    def setup_method(self):
        self.fake_redis = fakeredis.FakeRedis(decode_responses=False)

    def test_enqueue_and_get_job(self):
        queue = Queue("draftly:test", connection=self.fake_redis)
        sync_handler = make_sync_handler(_mock_workflow)

        job = queue.enqueue(
            sync_handler,
            kwargs={"org_id": "test-org"},
            job_id="test-integration-001",
        )

        assert job.id == "test-integration-001"
        assert job.is_queued

        fetched = Job.fetch("test-integration-001", connection=self.fake_redis)
        assert fetched == job

    def test_execute_job(self):
        queue = Queue("draftly:test", connection=self.fake_redis)
        sync_handler = make_sync_handler(_mock_workflow)

        queue.enqueue(
            sync_handler,
            kwargs={"org_id": "exec-org"},
            job_id="test-exec-001",
        )

        result = sync_handler(org_id="exec-org")
        assert result == {"status": "ok", "org_id": "exec-org"}

    def test_queue_routing(self):
        from draftly.app.composition.rq_jobs import get_queue_for_task

        assert get_queue_for_task("documentation.sync") == "scheduled"
        assert get_queue_for_task("github_pr") == "webhooks"
        assert get_queue_for_task("onboarding.initialize") == "default"


class TestInitLockReleaseInWorker:
    """Task 9 Step 4: the RQ worker releases the per-org init lock after an
    onboarding.initialize job finishes (success or failure)."""

    def _make_worker_and_queue(self, prefix="draftly") -> tuple[Queue, fakeredis.FakeRedis]:
        from workers.rq_worker import InitLockAwareWorker

        fake = fakeredis.FakeRedis(decode_responses=True)
        queue = Queue(f"{prefix}:default", connection=fake)
        worker = InitLockAwareWorker(
            [f"{prefix}:default"], connection=fake, serializer="json"
        )
        return queue, fake, worker

    def test_lock_released_after_onboarding_job_runs(self):
        from draftly.app.services.init_lock import INIT_LOCK_TTL_SECONDS

        queue, fake, worker = self._make_worker_and_queue()

        async def onboarding_workflow(org_id="", run_id=""):
            return {"status": "done"}

        job = queue.enqueue(
            make_sync_handler(onboarding_workflow),
            kwargs={"org_id": "org-1", "run_id": "run-1"},
            job_id="onb-run-1",
            meta={"task_name": "onboarding.initialize"},
        )
        # Simulate the API having acquired the lock before enqueueing.
        fake.set(init_lock_key("org-1"), "run-1", ex=INIT_LOCK_TTL_SECONDS)

        worker.execute_job(job, queue)

        assert fake.get(init_lock_key("org-1")) is None

    def test_non_onboarding_jobs_leave_lock_alone(self):
        queue, fake, worker = self._make_worker_and_queue()

        job = queue.enqueue(
            make_sync_handler(_mock_workflow),
            kwargs={"org_id": "org-1"},
            job_id="sync-1",
            meta={"task_name": "documentation.sync"},
        )
        fake.set(init_lock_key("org-1"), "run-1")

        worker.execute_job(job, queue)

        assert fake.get(init_lock_key("org-1")) == "run-1"

    def test_stale_lock_not_released_by_other_run(self):
        queue, fake, worker = self._make_worker_and_queue()

        async def onboarding_workflow(org_id="", run_id=""):
            return {"status": "done"}

        job = queue.enqueue(
            make_sync_handler(onboarding_workflow),
            kwargs={"org_id": "org-1", "run_id": "run-2"},
            job_id="onb-run-2",
            meta={"task_name": "onboarding.initialize"},
        )
        # A newer run owns the lock; this job must not clobber it.
        fake.set(init_lock_key("org-1"), "run-9-newer")

        worker.execute_job(job, queue)

        assert fake.get(init_lock_key("org-1")) == "run-9-newer"
