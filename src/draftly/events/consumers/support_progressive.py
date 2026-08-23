"""Progressively render streamed agent output into chat messages (spec §Phase 3).

Subscribes to a run's event stream and edits the target chat message at
most once per throttle window; the terminal ``workflow_result`` flushes
the remaining buffer. Edit failures are swallowed — progressive rendering
must never break delivery.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class SupportProgressiveRenderer:
    def __init__(
        self,
        client: Any,
        *,
        channel_ref: tuple[str, str],
        throttle_seconds: float = 1.0,
    ) -> None:
        self.client = client
        self.channel_id, self.message_id = channel_ref
        self.throttle = throttle_seconds

    async def run(self, bus: Any, run_id: str) -> None:
        buffer = ""
        last_edit = float("-inf")
        last_edited: str | None = None
        async for envelope in bus.subscribe(run_id):
            if envelope.type == "text_delta":
                buffer += str(envelope.payload.get("text", ""))
                now = time.monotonic()
                if now - last_edit >= self.throttle and buffer != last_edited:
                    await self._edit(buffer)
                    last_edit = now
                    last_edited = buffer
            elif envelope.type == "workflow_result":
                if buffer and buffer != last_edited:
                    await self._edit(buffer)
                return

    async def _edit(self, content: str) -> None:
        try:
            await self.client.edit_message(
                self.channel_id, self.message_id, content
            )
        except Exception:
            logger.warning("progressive_edit_failed", exc_info=True)
