"""Broadcast dashboard-relevant events via Redis pub/sub for frontend push."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

CHANNEL_PREFIX = "draftly:dashboard"


class DashboardBroadcaster:
    """Publish and subscribe to dashboard events per org."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._active_subs: dict[str, int] = {}

    def _channel(self, org_id: str) -> str:
        return f"{CHANNEL_PREFIX}:{org_id}"

    def _subscription_count(self, org_id: str) -> int:
        """Live subscriber count for an org channel (test/diagnostics hook)."""
        return self._active_subs.get(org_id, 0)

    async def broadcast(self, org_id: str, event_type: str, payload: dict) -> bool:
        try:
            message = json.dumps({"type": event_type, "payload": payload})
            await self._client.publish(self._channel(org_id), message)
            return True
        except Exception:
            logger.warning(
                "dashboard_broadcast_failed org=%s type=%s",
                org_id,
                event_type,
                exc_info=True,
            )
            return False

    async def subscribe(self, org_id: str) -> AsyncIterator[dict]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(self._channel(org_id))
        self._active_subs[org_id] = self._active_subs.get(org_id, 0) + 1
        try:
            async for message in pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                try:
                    data = message.get("data")
                    raw = (
                        data.decode()
                        if isinstance(data, (bytes, bytearray))
                        else str(data)
                    )
                    yield json.loads(raw)
                except Exception:
                    logger.warning(
                        "dashboard_bad_frame org=%s", org_id, exc_info=True
                    )
        finally:
            self._active_subs[org_id] = max(0, self._active_subs.get(org_id, 1) - 1)
            await self._close_pubsub(pubsub)

    async def _close_pubsub(self, pubsub: Any) -> None:
        try:
            await pubsub.unsubscribe()
            await pubsub.aclose()
        except Exception:
            pass
