"""Org-scoped dashboard events over Redis Streams (durable, resumable).

Replaces ``DashboardBroadcaster``'s pub/sub transport with Redis Streams so
dashboard SSE consumers can resume from a ``Last-Event-ID`` and never miss a
frame that arrived while no subscriber was connected. Produces the same frame
shape ``{"type", "payload"}`` the dashboard SSE route already emits, plus a
stream ``id`` for SSE resume.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

STREAM_PREFIX = "draftly:dashstream"
MAX_STREAM_LEN = 500  # bounded per-org history for resume + memory caps


def stream_key(org_id: str) -> str:
    return f"{STREAM_PREFIX}:{org_id}"


class DashboardStreamBus:
    """Publish and subscribe to dashboard events per org via Redis Streams."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def broadcast(self, org_id: str, event_type: str, payload: dict) -> bool:
        """Append a dashboard event to the org's stream. Same signature as the
        legacy ``broadcast`` so producers swap without change."""
        try:
            await self._client.xadd(
                stream_key(org_id),
                {
                    "type": event_type,
                    "payload": json.dumps(payload, default=str),
                },
                maxlen=MAX_STREAM_LEN,
            )
            return True
        except Exception:
            logger.warning(
                "dashboard_stream_broadcast_failed org=%s type=%s",
                org_id,
                event_type,
                exc_info=True,
            )
            return False

    async def subscribe(
        self,
        org_id: str,
        last_id: str = "0",
        block_ms: int = 15000,
    ) -> AsyncIterator[dict]:
        key = stream_key(org_id)
        while True:
            try:
                result = await self._client.xread(
                    {key: last_id}, count=50, block=block_ms
                )
                if not result:
                    continue
                for _stream_name, messages in result:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        try:
                            payload_str = fields.get("payload", "{}")
                            payload = (
                                json.loads(payload_str)
                                if isinstance(payload_str, str)
                                else {}
                            )
                            yield {
                                "type": fields.get("type", "message"),
                                "payload": payload,
                                "id": msg_id,
                            }
                        except Exception:
                            logger.warning(
                                "dashboard_stream_bad_frame org=%s", org_id,
                                exc_info=True,
                            )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning(
                    "dashboard_stream_subscribe_error org=%s", org_id, exc_info=True
                )
                await asyncio.sleep(1)
