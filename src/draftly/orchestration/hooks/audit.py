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
import time
from typing import Any

import structlog
from strands.hooks import (
    AfterInvocationEvent,
    AfterNodeCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeNodeCallEvent,
    HookProvider,
    HookRegistry,
)

from draftly.observability.metrics import Metrics
from draftly.observability.metrics import metrics as _default_metrics

logger = structlog.get_logger(__name__)

_metrics: Metrics = _default_metrics


class RunAuditLogger(HookProvider):
    """Persist per-step telemetry and audit rows for a run."""

    def __init__(self, audit_repo: Any = None, publisher: Any = None, jobs_repo: Any = None) -> None:
        self.audit_repo = audit_repo
        self.publisher = publisher
        self.jobs_repo = jobs_repo
        self._node_started_at: dict[str, float] = {}
        self._steps: list[dict[str, Any]] = []
        self._stream_pending: list[dict[str, Any]] = []
        self._run_meta: dict[str, Any] = {}
        self._seq = 0
        self._stream_seq = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.run_start)
        registry.add_callback(BeforeNodeCallEvent, self.node_start)
        registry.add_callback(AfterNodeCallEvent, self.node_end)
        registry.add_callback(AfterToolCallEvent, self.tool_end)
        registry.add_callback(AfterInvocationEvent, self.run_end)

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
        if self.jobs_repo is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(
                    _upsert_job_row(
                        self.jobs_repo,
                        job_id=str(run_id),
                        org_id=str(state.get("project_id", "")),
                        surface=str(state.get("source", "github")),
                        event_type=str(state.get("event_type", "unknown")),
                    )
                )
        logger.info("audit.run.start run_id=%s", run_id)

    def node_start(self, event: BeforeNodeCallEvent) -> None:
        self._node_started_at[event.node_id] = time.monotonic()
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        if run_id:
            self._stream_seq += 1
            self._stream_pending.append({
                "type": "node_start",
                "node_id": str(event.node_id),
                "payload": {"node_type": "agent"},
                "seq": self._stream_seq,
            })
            logger.info("audit.step.start run_id=%s node_id=%s", run_id, event.node_id)

    def node_end(self, event: AfterNodeCallEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        started = self._node_started_at.pop(event.node_id, None)
        duration_ms = round((time.monotonic() - started) * 1000) if started else None
        status = _status_of(event)
        detail = {"agent_name": str(event.node_id), "capabilities": [str(event.node_id)]}
        self._buffer_step(
            run_id=run_id,
            kind="node",
            name=str(event.node_id),
            status=status,
            duration_ms=duration_ms,
            detail=detail,
        )
        if run_id:
            self._stream_seq += 1
            self._stream_pending.append({
                "type": "node_stop",
                "node_id": str(event.node_id),
                "payload": {**detail, "status": status, "duration_ms": duration_ms},
                "seq": self._stream_seq,
            })
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
        status = _tool_status_of(event)
        self._buffer_step(
            run_id=run_id,
            kind="tool",
            name=str(tool_name or "unknown"),
            status=status,
            detail={"tool_name": str(tool_name or "unknown"), "status": status},
        )
        if run_id:
            self._stream_seq += 1
            self._stream_pending.append({
                "type": "tool_progress",
                "node_id": None,
                "payload": {
                    "name": str(tool_name or "unknown"),
                    "status": status,
                    "node_id": None,
                },
                "seq": self._stream_seq,
            })
            logger.info("audit.tool run_id=%s tool=%s", run_id, tool_name)

    def run_end(self, event: AfterInvocationEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        if not run_id:
            return
        meta = dict(self._run_meta)
        steps = list(self._steps)
        stream_pending = list(self._stream_pending)
        self._steps.clear()
        self._stream_pending.clear()
        self._run_meta.clear()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("audit_flush_skipped_no_loop run_id=%s", run_id)
            return
        if self.audit_repo is not None and steps:
            loop.create_task(_flush_run(self.audit_repo, str(run_id), meta, steps))
        if self.publisher is not None and stream_pending:
            loop.create_task(
                _flush_stream(
                    self.publisher, str(run_id),
                    meta.get("surface", ""), stream_pending,
                )
            )

    async def run_end_async(self, event: AfterInvocationEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        if not run_id:
            return
        if self.publisher is not None:
            from draftly.events.stream_envelope import StreamEnvelope
            for spec in self._stream_pending:
                await self.publisher.publish(
                    StreamEnvelope(
                        type=str(spec["type"]),
                        run_id=run_id,
                        surface=self._run_meta.get("surface", ""),
                        seq=int(spec.get("seq", 0)),
                        node_id=spec.get("node_id"),
                        payload=dict(spec.get("payload") or {}),
                    )
                )

    def _buffer_step(
        self,
        *,
        run_id: Any,
        kind: str,
        name: str,
        status: str,
        duration_ms: int | None = None,
        detail: dict[str, Any] | None = None,
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
                "detail": detail or {},
            }
        )


async def _flush_run(
    repo: Any,
    run_id: str,
    meta: dict[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    try:
        await repo.start_run(
            run_id=run_id,
            source=meta.get("source", "github"),
            event_type=meta.get("event_type", "unknown"),
            org_id=meta.get("org_id", ""),
        )
        kind_counters = {
            "node": "draftly_node_steps_total",
            "tool": "draftly_tool_steps_total",
        }
        for step in steps:
            await repo.record_step(run_id=run_id, **step)
            counter = kind_counters.get(str(step.get("kind", "")))
            if counter:
                _metrics.increment(counter)
        failed = [s for s in steps if s["status"] == "failed"]
        await repo.finish_run(
            run_id=run_id,
            status="failed" if failed else "completed",
            error=f"nodes failed: {failed}" if failed else None,
        )
    except Exception:
        logger.exception("audit_flush_failed run_id=%s", run_id)


async def _flush_stream(
    publisher: Any, run_id: str, surface: str, pending: list[dict[str, Any]]
) -> None:
    from draftly.events.stream_envelope import StreamEnvelope

    try:
        for spec in pending:
            await publisher.publish(
                StreamEnvelope(
                    type=str(spec["type"]),
                    run_id=run_id,
                    surface=surface,
                    seq=int(spec.get("seq", 0)),
                    node_id=spec.get("node_id"),
                    payload=dict(spec.get("payload") or {}),
                )
            )
    except Exception:
        logger.warning("audit_stream_flush_failed run_id=%s", run_id, exc_info=True)


async def _upsert_job_row(
    jobs_repo: Any, *, job_id: str, org_id: str, surface: str, event_type: str
) -> None:
    try:
        await jobs_repo.upsert_on_conflict(
            job_id=job_id,
            org_id=org_id,
            name=surface or event_type or "agent",
            job_type="agent",
            status="running",
        )
    except Exception:
        logger.warning("audit_job_row_upsert_failed run_id=%s", job_id, exc_info=True)


def _status_of(event: AfterNodeCallEvent) -> str:
    result = getattr(event, "result", None)
    status = getattr(result, "status", None)
    return str(status).rsplit(".", maxsplit=1)[-1].lower() if status else "completed"


def _tool_status_of(event: AfterToolCallEvent) -> str:
    result = getattr(event, "result", None)
    error = getattr(result, "get", lambda *_: None)("status") if result else None
    return "failed" if str(error) == "error" else "completed"
