"""Redis Streams-based event bus replacing pub/sub for durable delivery."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog

from draftly.events.stream_envelope import StreamEnvelope

logger = structlog.get_logger(__name__)

STREAM_PREFIX = "draftly:stream"
MAX_STREAM_LEN = 1000  # ~1000 events per run


def stream_key(run_id: str) -> str:
    return f"{STREAM_PREFIX}:{run_id}"


class RedisStreamBus:
    """Publish/subscribe workflow events via Redis Streams."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def publish(self, envelope: StreamEnvelope) -> bool:
        try:
            await self._client.xadd(
                stream_key(envelope.run_id),
                {
                    "seq": str(envelope.seq),
                    "type": envelope.type,
                    "node_id": envelope.node_id or "",
                    "surface": envelope.surface,
                    "payload": envelope.to_json(),
                    "ts": envelope.ts,
                },
                maxlen=MAX_STREAM_LEN,
            )
            return True
        except Exception:
            logger.warning(
                "stream_bus_publish_failed run_id=%s type=%s",
                envelope.run_id,
                envelope.type,
                exc_info=True,
            )
            return False

    async def subscribe(
        self,
        run_id: str,
        last_id: str = "0",
        block_ms: int = 15000,
    ) -> AsyncIterator[StreamEnvelope]:
        key = stream_key(run_id)

        while True:
            try:
                result = await self._client.xread(
                    {key: last_id}, count=10, block=block_ms
                )
                if not result:
                    continue

                for _stream_name, messages in result:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        try:
                            import json as _json
                            payload_str = fields.get("payload", "{}")
                            payload = (
                                _json.loads(payload_str)
                                if isinstance(payload_str, str)
                                else {}
                            )
                            envelope = StreamEnvelope(
                                type=fields.get("type", "unknown"),
                                run_id=run_id,
                                surface=fields.get("surface", ""),
                                seq=int(fields.get("seq", 0)),
                                ts=fields.get("ts", ""),
                                node_id=fields.get("node_id") or None,
                                payload=payload,
                            )
                            yield envelope
                        except Exception:
                            logger.warning(
                                "stream_bus_bad_frame run_id=%s", run_id, exc_info=True
                            )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("stream_bus_subscribe_error run_id=%s", run_id, exc_info=True)
                await asyncio.sleep(1)

    async def close(self) -> None:
        pass  # Client is managed externally
