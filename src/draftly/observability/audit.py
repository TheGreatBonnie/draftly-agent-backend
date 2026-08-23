"""Audit trail management (plan §9.3).

Complements ``draftly.security.audit`` (security events) with a
workflow-level audit trail: every run outcome and human decision is
recorded through the events repository when available, with an
in-memory fallback so callers never fail because of auditing.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class AuditTrail:
    """Append-only audit trail with a pluggable persistence sink."""

    def __init__(
        self,
        sink: Any = None,
        *,
        memory_limit: int = 1000,
    ) -> None:
        self.sink = sink
        self._memory: deque[dict[str, Any]] = deque(maxlen=memory_limit)

    async def record(
        self,
        *,
        actor: str,
        action: str,
        target: str = "",
        outcome: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one auditable action."""
        entry = {
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
            "details": details or {},
            "at": datetime.now(UTC).isoformat(),
        }
        self._memory.append(entry)
        if self.sink is not None:
            try:
                await self.sink(entry)
            except Exception:
                logger.exception("audit_persist_failed action=%s", action)
        return entry

    async def record_run(
        self,
        *,
        run_id: str,
        workflow: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.record(
            actor="system",
            action="workflow_run",
            target=run_id,
            outcome=status,
            details={"workflow": workflow, **(details or {})},
        )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent in-memory entries (repository holds the history)."""
        entries = list(self._memory)
        return list(reversed(entries[-limit:]))
