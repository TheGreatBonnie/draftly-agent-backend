import pytest
from fakeredis import aioredis

from draftly.events.stream_envelope import StreamEnvelope


@pytest.fixture
def fake_redis():
    return aioredis.FakeRedis(decode_responses=True)


def _make_envelope(seq: int = 1) -> StreamEnvelope:
    return StreamEnvelope(
        type="node_start",
        run_id="run-test",
        surface="documentation",
        seq=seq,
        ts="2026-01-01T00:00:00Z",
        node_id="classify",
        payload={"node_type": "agent"},
    )


@pytest.mark.asyncio
async def test_publish_adds_to_stream(fake_redis):
    from draftly.events.redis_stream_bus import RedisStreamBus

    bus = RedisStreamBus(fake_redis)
    result = await bus.publish(_make_envelope())
    assert result is True
    length = await fake_redis.xlen("draftly:stream:run-test")
    assert length == 1


@pytest.mark.asyncio
async def test_subscribe_reads_messages(fake_redis):
    from draftly.events.redis_stream_bus import RedisStreamBus

    bus = RedisStreamBus(fake_redis)
    await bus.publish(_make_envelope(seq=1))
    await bus.publish(_make_envelope(seq=2))

    messages = []
    async for envelope in bus.subscribe("run-test", block_ms=100):
        messages.append(envelope)
        if len(messages) >= 2:
            break
    assert len(messages) == 2
    assert messages[0].seq == 1
    assert messages[1].seq == 2


@pytest.mark.asyncio
async def test_subscribe_preserves_payload_roundtrip(fake_redis):
    """The payload must survive the publish -> subscribe round-trip intact.

    Regression: publish stored the whole envelope JSON in the stream's
    ``payload`` field, so subscribers received the envelope nested inside
    its own payload (``payload.payload``), breaking ``stage_progress`` /
    ``tool_progress`` / ``workflow_result`` consumers.
    """
    from draftly.events.redis_stream_bus import RedisStreamBus

    bus = RedisStreamBus(fake_redis)
    original = _make_envelope(seq=1)
    await bus.publish(original)

    async for envelope in bus.subscribe("run-test", block_ms=100):
        assert envelope.seq == 1
        assert envelope.type == "node_start"
        assert envelope.surface == "documentation"
        assert envelope.node_id == "classify"
        assert envelope.payload == {"node_type": "agent"}
        break


@pytest.mark.asyncio
async def test_publish_never_raises(fake_redis):
    from draftly.events.redis_stream_bus import RedisStreamBus

    class ExplodingClient:
        async def xadd(self, *args, **kwargs):
            raise ConnectionError("redis down")

    bus = RedisStreamBus(ExplodingClient())
    result = await bus.publish(_make_envelope())
    assert result is False
