"""Tests for RQ job registry and enqueue functions."""

from __future__ import annotations

from unittest.mock import MagicMock

from draftly.app.composition.rq_jobs import (
    QUEUE_MAP,
    build_rq_queues,
    get_queue_for_task,
)


class TestGetQueueForTask:
    def test_scheduled_tasks(self):
        assert get_queue_for_task("documentation.sync") == "scheduled"
        assert get_queue_for_task("evaluation.loop") == "scheduled"
        assert get_queue_for_task("memory.curation") == "scheduled"

    def test_webhook_tasks(self):
        assert get_queue_for_task("github_pr") == "webhooks"
        assert get_queue_for_task("github_release.enqueue") == "webhooks"
        assert get_queue_for_task("slack_support") == "webhooks"
        assert get_queue_for_task("slack_support.enqueue") == "webhooks"
        assert get_queue_for_task("discord_support.enqueue") == "webhooks"

    def test_default_tasks(self):
        assert get_queue_for_task("onboarding.initialize") == "default"
        assert get_queue_for_task("unknown_task") == "default"

    def test_all_registry_tasks_have_queues(self):
        from draftly.app.composition.workers import TASK_REGISTRY

        for task_name in TASK_REGISTRY:
            assert task_name in QUEUE_MAP, f"{task_name} missing from QUEUE_MAP"


class TestBuildRqQueues:
    def test_creates_queues(self):
        mock_conn = MagicMock()
        queues = build_rq_queues(mock_conn, prefix="test")
        assert "scheduled" in queues
        assert "webhooks" in queues
        assert "default" in queues
        assert len(queues) == 3
