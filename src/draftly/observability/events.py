"""Graph event streaming to SSE/WebSocket (plan §9.3).

``stream_graph_events`` adapts ``graph.stream_async`` into SSE-shaped
dicts; ``EventStream`` fans events out to subscribed clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


async def stream_graph_events(
    graph: Any,
    task: Any,
    invocation_state: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream graph events as SSE-ready payloads."""
    async for event in graph.stream_async(task, invocation_state=invocation_state):
        yield {
            "event": str(event.get("event_type", "unknown"))
            if isinstance(event, dict)
            else "unknown",
            "data": event,
        }


class EventStream:
    """In-process pub/sub hub for live workflow events."""

    def __init__(self, *, max_queue: int = 256) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._max_queue = max_queue

    def subscribe(self, channel: str = "default") -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers[channel].add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        self._subscribers[channel].discard(queue)
        if not self._subscribers[channel]:
            del self._subscribers[channel]

    async def publish(self, channel: str, event: dict[str, Any]) -> int:
        """Fan an event out to subscribers; returns delivery count."""
        payload = {
            "channel": channel,
            "at": datetime.now(UTC).isoformat(),
            **event,
        }
        delivered = 0
        for queue in list(self._subscribers.get(channel, ())):
            try:
                queue.put_nowait(payload)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning("event_stream_subscriber_slow channel=%s", channel)
        return delivered

    @staticmethod
    def format_sse(event: dict[str, Any]) -> str:
        """Render one event as a Server-Sent Events frame."""
        name = event.get("event", "message")
        return f"event: {name}\ndata: {json.dumps(event, default=str)}\n\n"
