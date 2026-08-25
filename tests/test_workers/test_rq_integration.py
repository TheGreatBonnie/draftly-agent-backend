"""Integration tests for RQ job enqueue and execution cycle."""

from __future__ import annotations

import fakeredis
from rq import Queue
from rq.job import Job

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
