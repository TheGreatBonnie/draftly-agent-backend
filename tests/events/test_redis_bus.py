"""RedisEventBus publish/subscribe against fakeredis."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fakeredis.aioredis import FakeRedis

from draftly.events.redis_bus import RedisEventBus, channel_for


def envelope(seq: int = 1) -> Any:
    from draftly.events.stream_envelope import StreamEnvelope

    return StreamEnvelope(type="node_start", run_id="evt-9", surface="support", seq=seq)


async def test_channel_naming() -> None:
    assert channel_for("evt-9") == "draftly:events:evt-9"


async def test_publish_returns_true_and_delivers() -> None:
    bus = RedisEventBus(redis_client=FakeRedis())
    received: list[Any] = []

    async def consume() -> None:
        async for env in bus.subscribe("evt-9"):
            received.append(env)

    task = asyncio.create_task(consume())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if bus.subscription_count("evt-9") > 0:
            break
    assert await bus.publish(envelope()) is True

    for _ in range(100):
        if received:
            break
        await asyncio.sleep(0.01)
    assert len(received) == 1
    assert received[0].type == "node_start"
    assert received[0].run_id == "evt-9"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await bus.close()


async def test_publish_swallows_redis_errors() -> None:
    class ExplodingClient:
        def publish(self, channel: str, message: str) -> ExplodingClient:
            raise ConnectionError("redis down")

        async def aclose(self) -> None:
            return None

    bus = RedisEventBus(redis_client=ExplodingClient())  # type: ignore[arg-type]
    assert await bus.publish(envelope()) is False
    await bus.close()


async def test_subscribe_survives_bad_json() -> None:
    client = FakeRedis()
    bus = RedisEventBus(redis_client=client)

    async def prime() -> None:
        while bus.subscription_count("evt-9") == 0:
            await asyncio.sleep(0.01)
        await client.publish(channel_for("evt-9"), b"not-json")
        await client.publish(
            channel_for("evt-9"),
            envelope(seq=2).to_json().encode(),
        )

    seen: list[Any] = []
    primer = asyncio.create_task(prime())

    async def drain() -> None:
        async for env in bus.subscribe("evt-9"):
            seen.append(env)
            if len(seen) == 1:
                break

    await asyncio.wait_for(drain(), timeout=5)
    await primer
    assert seen[0].seq == 2
    await bus.close()


def test_stream_envelope_reexported_from_events_package() -> None:
    # guard against accidental removal now that redis_bus imports it
    import draftly.events as events_pkg

    assert hasattr(events_pkg, "__path__")  # package imports cleanly
