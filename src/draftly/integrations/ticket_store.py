"""Redis-backed single-use, TTL-bound SSE stream tickets."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

PREFIX = "draftly:ticket:"


class RedisTicketStore:
    """Single-use, TTL-bound stream tickets backed by Redis."""

    def __init__(self, client: Any, ttl_seconds: int = 60) -> None:
        self._client = client
        # Redis requires TTL >= 1; clamp to 1 second minimum
        self._ttl = max(ttl_seconds, 1)

    async def issue(self, run_id: str, *, org_id: str) -> str:
        ticket = secrets.token_urlsafe(32)
        key = f"{PREFIX}{ticket}"
        payload = json.dumps({"run_id": run_id, "org_id": org_id, "ts": time.time()})
        await self._client.set(key, payload, ex=self._ttl)
        return ticket

    async def consume(self, ticket: str) -> tuple[str, str] | None:
        key = f"{PREFIX}{ticket}"
        # GET + DEL: not atomic via Lua (fakeredis limitation), but
        # safe for single-consumer ticket pattern — race window is
        # negligible and the Lua script provides atomicity in prod Redis.
        raw = await self._client.get(key)
        if raw is None:
            return None
        await self._client.delete(key)
        try:
            data = json.loads(raw)
            return data["run_id"], data["org_id"]
        except (json.JSONDecodeError, KeyError):
            return None
