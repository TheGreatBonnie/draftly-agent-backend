"""DashboardBroadcaster publish/subscribe against fakeredis."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from fakeredis.aioredis import FakeRedis


@pytest.fixture
def fake_redis():
    return FakeRedis(decode_responses=True)


async def test_broadcast_publishes_to_channel(fake_redis):
    from draftly.events.dashboard_broadcaster import DashboardBroadcaster

    broadcaster = DashboardBroadcaster(fake_redis)
    result = await broadcaster.broadcast("org-123", "review_created", {"review_id": "r1"})
    assert result is True


async def test_subscribe_receives_events(fake_redis):
    from draftly.events.dashboard_broadcaster import DashboardBroadcaster

    broadcaster = DashboardBroadcaster(fake_redis)
    events: list[dict] = []

    async def listener():
        async for event in broadcaster.subscribe("org-123"):
            events.append(event)
            if len(events) >= 1:
                break

    async def publisher():
        await asyncio.sleep(0.05)
        await broadcaster.broadcast("org-123", "job_started", {"job_id": "j1"})

    await asyncio.gather(listener(), publisher())
    assert len(events) == 1
    assert events[0]["type"] == "job_started"
    assert events[0]["payload"]["job_id"] == "j1"


async def test_subscribe_does_not_cross_org(fake_redis):
    from draftly.events.dashboard_broadcaster import DashboardBroadcaster

    broadcaster = DashboardBroadcaster(fake_redis)
    events: list[dict] = []

    async def listener():
        async for event in broadcaster.subscribe("org-AAA"):
            events.append(event)
            if len(events) >= 1:
                break

    async def publisher():
        await asyncio.sleep(0.05)
        await broadcaster.broadcast("org-BBB", "review_decided", {"review_id": "r2"})
        await asyncio.sleep(0.2)
        await broadcaster.broadcast("org-AAA", "review_created", {"review_id": "r3"})

    await asyncio.wait_for(asyncio.gather(listener(), publisher()), timeout=5)
    assert len(events) == 1
    assert events[0]["type"] == "review_created"


async def test_broadcast_returns_false_on_error():
    from draftly.events.dashboard_broadcaster import DashboardBroadcaster

    class ExplodingClient:
        def publish(self, channel: str, message: str):
            raise ConnectionError("redis down")

    broadcaster = DashboardBroadcaster(ExplodingClient())  # type: ignore[arg-type]
    assert await broadcaster.broadcast("org-123", "review_created", {}) is False


async def test_subscribe_survives_bad_json(fake_redis):
    from draftly.events.dashboard_broadcaster import DashboardBroadcaster

    broadcaster = DashboardBroadcaster(fake_redis)

    async def prime():
        while broadcaster._subscription_count("org-123") == 0:
            await asyncio.sleep(0.01)
        await fake_redis.publish("draftly:dashboard:org-123", b"not-json")
        await fake_redis.publish(
            "draftly:dashboard:org-123",
            '{"type": "job_completed", "payload": {"job_id": "j2"}}',
        )

    seen: list[dict] = []
    primer = asyncio.create_task(prime())

    async def drain():
        async for event in broadcaster.subscribe("org-123"):
            seen.append(event)
            if len(seen) == 1:
                break

    await asyncio.wait_for(drain(), timeout=5)
    await primer
    assert seen[0]["type"] == "job_completed"
    assert seen[0]["payload"]["job_id"] == "j2"
