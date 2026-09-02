"""DashboardStreamBus publish/subscribe over Redis Streams (fakeredis)."""

from __future__ import annotations

import asyncio

import pytest
from fakeredis.aioredis import FakeRedis


@pytest.fixture
def fake_redis():
    return FakeRedis(decode_responses=True)


async def test_broadcast_publishes_to_org_stream(fake_redis):
    from draftly.events.dashboard_stream_bus import DashboardStreamBus

    bus = DashboardStreamBus(fake_redis)
    ok = await bus.broadcast("org-123", "review_created", {"review_id": "r1"})
    assert ok is True
    length = await fake_redis.xlen("draftly:dashstream:org-123")
    assert length == 1


async def test_subscribe_receives_live_events(fake_redis):
    from draftly.events.dashboard_stream_bus import DashboardStreamBus

    bus = DashboardStreamBus(fake_redis)
    events: list[dict] = []

    async def listener():
        async for event in bus.subscribe("org-123"):
            events.append(event)
            if len(events) >= 1:
                break

    async def publisher():
        await asyncio.sleep(0.05)
        await bus.broadcast("org-123", "job_started", {"job_id": "j1"})

    await asyncio.gather(listener(), publisher())
    assert len(events) == 1
    assert events[0]["type"] == "job_started"
    assert events[0]["payload"]["job_id"] == "j1"
    assert events[0]["id"]  # stream message id present for SSE resume


async def test_subscribe_does_not_cross_org(fake_redis):
    from draftly.events.dashboard_stream_bus import DashboardStreamBus

    bus = DashboardStreamBus(fake_redis)
    events: list[dict] = []

    async def listener():
        async for event in bus.subscribe("org-AAA"):
            events.append(event)
            if len(events) >= 1:
                break

    async def publisher():
        await asyncio.sleep(0.05)
        await bus.broadcast("org-BBB", "review_decided", {"review_id": "r2"})
        await asyncio.sleep(0.2)
        await bus.broadcast("org-AAA", "review_created", {"review_id": "r3"})

    await asyncio.wait_for(asyncio.gather(listener(), publisher()), timeout=5)
    assert len(events) == 1
    assert events[0]["type"] == "review_created"


async def test_broadcast_returns_false_on_error(fake_redis):
    from draftly.events.dashboard_stream_bus import DashboardStreamBus

    class ExplodingClient:
        async def xadd(self, key: str, fields: dict, **kwargs):
            raise ConnectionError("redis down")

    bus = DashboardStreamBus(ExplodingClient())  # type: ignore[arg-type]
    assert await bus.broadcast("org-123", "review_created", {}) is False


async def test_subscribe_resumes_from_last_id(fake_redis):
    from draftly.events.dashboard_stream_bus import DashboardStreamBus

    bus = DashboardStreamBus(fake_redis)
    await bus.broadcast("org-123", "workflow:changed", {"run_id": "r1", "status": "running"})
    await bus.broadcast("org-123", "workflow:changed", {"run_id": "r1", "status": "completed"})

    # Consume from "0" (start of stream) and capture ids.
    ids: list[str] = []
    consumed: list[dict] = []

    async def drain_from_zero():
        async for event in bus.subscribe("org-123", last_id="0"):
            ids.append(event["id"])
            consumed.append(event)
            if len(consumed) >= 2:
                break

    await asyncio.wait_for(drain_from_zero(), timeout=5)
    assert len(consumed) == 2
    assert len(ids) == 2

    # Resume from the first id should only yield the second message.
    resumed: list[dict] = []

    async def drain_from_first():
        async for event in bus.subscribe("org-123", last_id=ids[0]):
            resumed.append(event)
            break

    await asyncio.wait_for(drain_from_first(), timeout=5)
    assert len(resumed) == 1
    assert resumed[0]["payload"]["status"] == "completed"


async def test_subscribe_survives_bad_json(fake_redis):
    from draftly.events.dashboard_stream_bus import DashboardStreamBus

    bus = DashboardStreamBus(fake_redis)
    # Junk field value must not crash the subscriber; it skips the frame.
    await fake_redis.xadd(
        "draftly:dashstream:org-123", {"type": "x", "payload": "not-json"}
    )
    await bus.broadcast("org-123", "job_completed", {"job_id": "j2"})

    seen: list[dict] = []

    async def drain():
        async for event in bus.subscribe("org-123", last_id="0"):
            seen.append(event)
            if len(seen) >= 1:
                break

    await asyncio.wait_for(drain(), timeout=5)
    assert seen[0]["type"] == "job_completed"
    assert seen[0]["payload"]["job_id"] == "j2"
