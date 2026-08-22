"""Audit hook: persists per-step telemetry for a run (plan §10.2).

Writes ``agent_runs`` / ``agent_steps`` rows through an injected audit
repository. Strands hooks are synchronous callbacks, so events are
buffered in memory and flushed asynchronously at invocation end — the
graph never blocks on audit I/O. Without a repository the hook degrades
to structured log lines so graphs remain observable offline (no DB, no
model keys).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from strands.hooks import (
    AfterInvocationEvent,
    AfterNodeCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeNodeCallEvent,
    HookProvider,
    HookRegistry,
)

logger = logging.getLogger(__name__)


class RunAuditLogger(HookProvider):
    """Persist per-step telemetry and audit rows for a run."""

    def __init__(self, audit_repo: Any = None) -> None:
        self.audit_repo = audit_repo
        self._node_started_at: dict[str, float] = {}
        self._steps: list[dict[str, Any]] = []
        self._run_meta: dict[str, Any] = {}
        self._seq = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.run_start)
        registry.add_callback(BeforeNodeCallEvent, self.node_start)
        registry.add_callback(AfterNodeCallEvent, self.node_end)
        registry.add_callback(AfterToolCallEvent, self.tool_end)
        registry.add_callback(AfterInvocationEvent, self.run_end)

    # --------------------------------------------------------------
    # Synchronous hook callbacks — buffer only, no I/O.
    # --------------------------------------------------------------

    def run_start(self, event: BeforeInvocationEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        if not run_id:
            return
        self._run_meta = {
            "run_id": str(run_id),
            "source": str(state.get("source", "github")),
            "event_type": str(state.get("event_type", "unknown")),
            "org_id": str(state.get("project_id", "")),
        }
        logger.info("audit.run.start run_id=%s", run_id)

    def node_start(self, event: BeforeNodeCallEvent) -> None:
        run_id = (event.invocation_state or {}).get("run_id")
        self._node_started_at[event.node_id] = time.monotonic()
        if run_id:
            logger.info("audit.step.start run_id=%s node_id=%s", run_id, event.node_id)

    def node_end(self, event: AfterNodeCallEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        started = self._node_started_at.pop(event.node_id, None)
        duration_ms = round((time.monotonic() - started) * 1000) if started else None
        status = _status_of(event)
        self._buffer_step(
            run_id=run_id,
            kind="node",
            name=str(event.node_id),
            status=status,
            duration_ms=duration_ms,
        )
        if run_id:
            logger.info(
                "audit.step.end run_id=%s node_id=%s duration_ms=%s",
                run_id,
                event.node_id,
                duration_ms,
            )

    def tool_end(self, event: AfterToolCallEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        tool_name = getattr(event.tool_use, "get", lambda *_: None)("name")
        self._buffer_step(
            run_id=run_id,
            kind="tool",
            name=str(tool_name or "unknown"),
            status=_tool_status_of(event),
        )
        if run_id:
            logger.info("audit.tool run_id=%s tool=%s", run_id, tool_name)

    # --------------------------------------------------------------
    # Async flush at invocation end.
    # --------------------------------------------------------------

    def run_end(self, event: AfterInvocationEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        if not run_id or self.audit_repo is None:
            return
        meta = dict(self._run_meta)
        steps = list(self._steps)
        self._steps.clear()
        self._run_meta.clear()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("audit_flush_skipped_no_loop run_id=%s", run_id)
            return
        loop.create_task(_flush_run(self.audit_repo, str(run_id), meta, steps))

    # --------------------------------------------------------------
    # Internals
    # --------------------------------------------------------------

    def _buffer_step(
        self,
        *,
        run_id: Any,
        kind: str,
        name: str,
        status: str,
        duration_ms: int | None = None,
    ) -> None:
        if not run_id:
            return
        self._seq += 1
        self._steps.append(
            {
                "seq": self._seq,
                "kind": kind,
                "name": name,
                "status": status,
                "duration_ms": duration_ms,
            }
        )


async def _flush_run(
    repo: Any,
    run_id: str,
    meta: dict[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    """Write one agent_runs row plus its agent_steps rows."""
    try:
        await repo.start_run(
            run_id=run_id,
            source=meta.get("source", "github"),
            event_type=meta.get("event_type", "unknown"),
            org_id=meta.get("org_id", ""),
        )
        for step in steps:
            await repo.record_step(run_id=run_id, **step)
        failed = [s for s in steps if s["status"] == "failed"]
        await repo.finish_run(
            run_id=run_id,
            status="failed" if failed else "completed",
            error=f"nodes failed: {failed}" if failed else None,
        )
    except Exception:
        logger.exception("audit_flush_failed run_id=%s", run_id)


def _status_of(event: AfterNodeCallEvent) -> str:
    result = getattr(event, "result", None)
    status = getattr(result, "status", None)
    return str(status).rsplit(".", maxsplit=1)[-1].lower() if status else "completed"


def _tool_status_of(event: AfterToolCallEvent) -> str:
    result = getattr(event, "result", None)
    error = getattr(result, "get", lambda *_: None)("status") if result else None
    return "failed" if str(error) == "error" else "completed"
