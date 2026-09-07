"""§9.3 verification: support events dispatch through the durable worker path.

Slack/Discord ingress must not call ``runner.run`` directly. Enriched support
events go through the shared ``enqueue_support_event`` boundary: RQ when
enabled, the registered in-process task fallback otherwise.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import fakeredis

from draftly.app.composition.rq_jobs import (
    build_rq_queues,
    enqueue_support_event,
    get_queue_for_task,
)

SLACK_EVENT = {
    "event_id": "slack-T1-C1-1",
    "event_type": "slack.message",
    "source": "slack",
    "project_id": "org-1",
    "team_id": "T1",
    "channel": "C1",
    "thread_ts": "1",
}

DISCORD_EVENT = {
    "event_id": "discord-C9-9",
    "event_type": "discord.message",
    "source": "discord",
    "project_id": "org-1",
    "guild_id": "G1",
    "channel": "C9",
    "thread_ts": "9",
}


class _FakeWorker:
    def __init__(self):
        self.calls: list[tuple] = []

    async def run_task(self, task_name: str, **kwargs):
        self.calls.append((task_name, kwargs))


class _FakeAppState:
    def __init__(self, *, rq_enabled, queues=None, task_handlers=None, worker=None):
        self.settings = SimpleNamespace(rq_enabled=rq_enabled)
        self.rq_queues = queues
        self.task_handlers = task_handlers
        self.worker = worker


async def _ok_handler(**kwargs):
    return {"status": "executed", **kwargs}


class TestSupportTaskRegistration:
    def test_support_tasks_are_registered(self):
        from draftly.app.composition.workers import TASK_REGISTRY

        assert TASK_REGISTRY["slack_support.enqueue"] == "slack_support"
        assert TASK_REGISTRY["discord_support.enqueue"] == "discord_support"

    def test_support_tasks_use_webhooks_queue(self):
        assert get_queue_for_task("slack_support.enqueue") == "webhooks"
        assert get_queue_for_task("discord_support.enqueue") == "webhooks"


class TestEnqueueSupportEvent:
    async def test_slack_event_enqueues_to_webhooks_when_rq_enabled(self):
        queues = build_rq_queues(fakeredis.FakeRedis())
        app_state = _FakeAppState(
            rq_enabled=True,
            queues=queues,
            task_handlers={"slack_support.enqueue": _ok_handler},
        )

        ok = await enqueue_support_event(SLACK_EVENT, app_state=app_state)

        assert ok is True
        assert queues["webhooks"].count == 1
        queued = queues["webhooks"].get_job_ids()
        job = queues["webhooks"].fetch_job(queued[0])
        assert job.kwargs.get("name") == "slack_support.enqueue"
        assert job.kwargs.get("event", {}).get("project_id") == "org-1"

    async def test_discord_event_enqueues_to_webhooks_when_rq_enabled(self):
        queues = build_rq_queues(fakeredis.FakeRedis())
        app_state = _FakeAppState(
            rq_enabled=True,
            queues=queues,
            task_handlers={"discord_support.enqueue": _ok_handler},
        )

        ok = await enqueue_support_event(DISCORD_EVENT, app_state=app_state)

        assert ok is True
        assert queues["webhooks"].count == 1
        queued = queues["webhooks"].get_job_ids()
        job = queues["webhooks"].fetch_job(queued[0])
        assert job.kwargs.get("name") == "discord_support.enqueue"
        assert job.kwargs.get("event", {}).get("project_id") == "org-1"

    async def test_fallback_schedules_inprocess_task(self):
        worker = _FakeWorker()
        app_state = _FakeAppState(rq_enabled=False, worker=worker)

        ok = await enqueue_support_event(SLACK_EVENT, app_state=app_state)

        assert ok is True
        await asyncio.sleep(0)
        assert len(worker.calls) == 1
        task_name, kwargs = worker.calls[0]
        assert task_name == "slack_support.enqueue"
        assert kwargs["event"]["event_type"] == "slack.message"

    async def test_no_runtime_returns_false(self):
        ok = await enqueue_support_event(SLACK_EVENT, app_state=None)

        assert ok is False

    async def test_runtime_without_worker_rq_disabled_returns_false(self):
        app_state = _FakeAppState(rq_enabled=False, worker=None)

        ok = await enqueue_support_event(SLACK_EVENT, app_state=app_state)

        assert ok is False

    async def test_unsupported_source_returns_false(self):
        app_state = _FakeAppState(rq_enabled=True, worker=_FakeWorker())

        ok = await enqueue_support_event(
            {**SLACK_EVENT, "source": "carrier-pigeon"},
            app_state=app_state,
        )

        assert ok is False


def test_support_task_for_event_maps_platforms():
    from draftly.app.composition.rq_jobs import support_task_for_event

    assert support_task_for_event({"source": "slack"}) == "slack_support.enqueue"
    assert support_task_for_event({"source": "discord"}) == "discord_support.enqueue"
    assert support_task_for_event({"source": "github"}) is None
