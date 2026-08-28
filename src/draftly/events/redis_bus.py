"""Async Redis pub/sub wrapper for workflow event envelopes.

The only module allowed to import redis. Publishing NEVER raises: a
streaming outage must not fail a workflow (spec §Error handling).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import structlog

from draftly.events.stream_envelope import StreamEnvelope

logger = structlog.get_logger(__name__)

CHANNEL_PREFIX = "draftly:events"


def channel_for(run_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{run_id}"


class RedisEventBus:
    """Publish envelopes to / subscribe iterators over run channels."""

    def __init__(self, redis_client: Any = None, url: str | None = None) -> None:
        if redis_client is not None:
            self._client = redis_client
        else:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(url or "redis://localhost:6379/0")
        self._pubsubs: dict[str, set[asyncio.Task[None]]] = defaultdict(set)
        self._active_subs: dict[str, int] = defaultdict(int)
        self._closed = False

    def subscription_count(self, run_id: str) -> int:
        """Live subscriber count for a run channel (test/diagnostics hook)."""
        return self._active_subs.get(run_id, 0)

    async def publish(self, envelope: StreamEnvelope) -> bool:
        logger.debug("redis_bus_publish", run_id=envelope.run_id, type=envelope.type, seq=envelope.seq)
        try:
            await self._client.publish(channel_for(envelope.run_id), envelope.to_json())
            return True
        except Exception:
            logger.warning(
                "event_bus_publish_failed run_id=%s type=%s",
                envelope.run_id,
                envelope.type,
                exc_info=True,
            )
            return False

    async def subscribe(self, run_id: str) -> AsyncIterator[StreamEnvelope]:
        logger.debug("redis_bus_subscribe_start", run_id=run_id, channel=channel_for(run_id))
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel_for(run_id))
        self._active_subs[run_id] += 1
        logger.debug("redis_bus_subscribed", run_id=run_id, active_subs=self._active_subs[run_id])
        try:
            async for message in pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                try:
                    data = message.get("data")
                    raw = data.decode() if isinstance(data, bytes | bytearray) else str(data)
                    envelope = StreamEnvelope.from_json(raw)
                    logger.debug("redis_bus_message", run_id=run_id, type=envelope.type, seq=envelope.seq)
                    yield envelope
                except Exception:
                    logger.warning("event_bus_bad_frame run_id=%s", run_id, exc_info=True)
        finally:
            self._active_subs[run_id] = max(0, self._active_subs[run_id] - 1)
            await self._close_pubsub(pubsub)
            logger.debug("redis_bus_unsubscribe", run_id=run_id, active_subs=self._active_subs[run_id])

    async def _close_pubsub(self, pubsub: Any) -> None:
        try:
            await pubsub.unsubscribe()
            await pubsub.aclose()
        except Exception:
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._client.aclose()
        except AttributeError:
            try:
                await self._client.close()
            except Exception:
                pass
        except Exception:
            pass
