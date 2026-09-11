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
import re
import time
from typing import Any

import structlog
from strands.hooks import (
    AfterMultiAgentInvocationEvent,
    AfterNodeCallEvent,
    AfterToolCallEvent,
    BeforeMultiAgentInvocationEvent,
    BeforeNodeCallEvent,
    HookProvider,
    HookRegistry,
)

from draftly.agents.catalog import agent_id_for_node
from draftly.observability.metrics import Metrics
from draftly.observability.metrics import metrics as _default_metrics
from draftly.steering.redaction import redact_value

logger = structlog.get_logger(__name__)

_metrics: Metrics = _default_metrics


class RunAuditLogger(HookProvider):
    """Persist per-step telemetry and audit rows for a run."""

    def __init__(
        self,
        audit_repo: Any = None,
        publisher: Any = None,
        jobs_repo: Any = None,
    ) -> None:
        self.audit_repo = audit_repo
        self.publisher = publisher
        self.jobs_repo = jobs_repo
        self._node_started_at: dict[str, float] = {}
        self._steps: list[dict[str, Any]] = []
        self._stream_pending: list[dict[str, Any]] = []
        self._run_meta: dict[str, Any] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}
        self._seq = 0
        self._stream_seq = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        # The workflow drives Strands multi-agent Graphs (not raw Agents), so
        # runs open/close on the multi-agent Invocation events; single-agent
        # Invocation events are never emitted by a Graph (§10.3 regression).
        registry.add_callback(BeforeMultiAgentInvocationEvent, self.run_start)
        registry.add_callback(BeforeNodeCallEvent, self.node_start)
        registry.add_callback(AfterNodeCallEvent, self.node_end)
        registry.add_callback(AfterToolCallEvent, self.tool_end)
        registry.add_callback(AfterMultiAgentInvocationEvent, self.run_end)

    def run_start(self, event: BeforeMultiAgentInvocationEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        if not run_id:
            return
        self._run_meta = {
            "run_id": str(run_id),
            "source": str(state.get("source", "github")),
            "event_type": str(state.get("event_type", "unknown")),
            "org_id": str(state.get("project_id", "")),
            "surface": str(state.get("surface", "")),
            "workflow_key": state.get("workflow_key"),
            "definition_id": state.get("definition_id"),
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
        if self.audit_repo is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                # Durable "running" row before any step: an interrupted run
                # still leaves a trace behind in agent_runs.
                loop.create_task(_open_run(self.audit_repo, dict(self._run_meta)))
        logger.info("audit_run_start", run_id=run_id)

    def node_start(self, event: BeforeNodeCallEvent) -> None:
        self._node_started_at[event.node_id] = time.monotonic()
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        agent_id = agent_id_for_node(str(state.get("surface", "")), str(event.node_id))
        if run_id:
            self._stream_seq += 1
            self._stream_pending.append({
                "type": "node_start",
                "node_id": str(event.node_id),
                "payload": {"node_type": "agent", "agent_id": agent_id,
                            "surface": str(state.get("surface", ""))},
                "seq": self._stream_seq,
            })
            logger.info(
                "audit_step_start",
                run_id=run_id,
                node_id=event.node_id,
            )

    def node_end(self, event: AfterNodeCallEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        started = self._node_started_at.pop(event.node_id, None)
        duration_ms = round((time.monotonic() - started) * 1000) if started else None
        status = _status_of(event)
        surface = str(state.get("surface", ""))
        node_id = str(event.node_id)
        agent_id = agent_id_for_node(surface, node_id)
        detail = {
            "agent_name": node_id,
            "agent_id": agent_id,
            "node_id": node_id,
            "surface": surface,
            "capabilities": [node_id],
        }
        self._buffer_step(
            run_id=run_id,
            kind="node",
            name=str(event.node_id),
            status=status,
            duration_ms=duration_ms,
            detail=detail,
            agent_id=agent_id,
            node_id=node_id,
            surface=surface,
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
                "audit_step_end",
                run_id=run_id,
                node_id=event.node_id,
                duration_ms=duration_ms,
            )

    def tool_end(self, event: AfterToolCallEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        tool_name = getattr(event.tool_use, "get", lambda *_: None)("name")
        status = _tool_status_of(event)
        surface = str(state.get("surface", ""))
        self._buffer_step(
            run_id=run_id,
            kind="tool",
            name=str(tool_name or "unknown"),
            status=status,
            detail={"tool_name": str(tool_name or "unknown"), "status": status},
            agent_id=agent_id_for_node(surface, None),
            node_id=None,
            surface=surface,
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
            logger.info("audit_tool", run_id=run_id, tool=tool_name)

    def run_end(self, event: AfterMultiAgentInvocationEvent) -> None:
        state = event.invocation_state or {}
        # The graph fires AfterMultiAgentInvocationEvent WITHOUT
        # invocation_state (strands 1.52.0 Graph._execute_graph finally
        # block); fall back to the run id recorded at run_start so the
        # flush still persists.
        run_id = state.get("run_id") or self._run_meta.get("run_id")
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
            logger.warning("audit_flush_skipped_no_loop", run_id=run_id)
            return
        if self.audit_repo is not None:
            task = loop.create_task(_flush_run(self.audit_repo, str(run_id), meta, steps))
            self._flush_tasks[str(run_id)] = task
            task.add_done_callback(lambda _t: self._flush_tasks.pop(str(run_id), None))
            logger.info(
                "audit_flush_scheduled",
                run_id=str(run_id),
                steps=len(steps),
            )
        if self.publisher is not None and stream_pending:
            loop.create_task(
                _flush_stream(
                    self.publisher, str(run_id),
                    meta.get("surface", ""), stream_pending,
                )
            )

    async def drain_run(self, run_id: Any) -> None:
        """Await the pending DB flush so agent_runs persists before the runner returns.

        The runner awaits this at the end of run/resume so audit rows survive
        event-loop teardown (fire-and-forget tasks are otherwise cancelled).
        """
        task = self._flush_tasks.pop(str(run_id), None)
        if task is None:
            return
        try:
            await task
        except Exception:
            logger.warning("audit_drain_failed", run_id=str(run_id), exc_info=True)
        else:
            logger.info("audit_flush_drained", run_id=str(run_id))

    async def run_end_async(self, event: AfterMultiAgentInvocationEvent) -> None:
        state = event.invocation_state or {}
        run_id = state.get("run_id") or self._run_meta.get("run_id")
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
        agent_id: str | None = None,
        node_id: str | None = None,
        surface: str = "",
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
                "agent_id": agent_id,
                "node_id": node_id,
                "surface": surface,
            }
        )


_REASON_SECRET_RE = re.compile(
    r"(?P<key>[\w.-]*?(?:token|secret|password|passwd|api[_-]?key|"
    r"authorization|access[_-]?key|credential|client[_-]?secret|"
    r"signing[_-]?secret))\s*=\s*[\w\-.:~/+%@]{3,}"
)


def _scrub_reason(reason: str) -> str:
    """Replace embedded credential values in a reason string."""
    if not reason:
        return reason
    return _REASON_SECRET_RE.sub(
        lambda match: f"{match.group('key')}=[REDACTED]", reason
    )


def _decision_detail(decision: Any, *, reason_max_chars: int) -> dict[str, Any]:
    """Project one steering decision into bounded, redacted audit detail."""
    return {
        "phase": getattr(getattr(decision, "phase", None), "value", str(decision.phase)),
        "action": getattr(getattr(decision, "kind", None), "value", str(decision.kind)),
        "role": getattr(decision.role, "value", None) if decision.role else None,
        "rule": decision.rule or "",
        "reason": _scrub_reason((decision.reason or "")[:reason_max_chars]),
        "interrupt_id": decision.interrupt_id,
    }


class SteeringAudit:
    """Write bounded steering decisions to ``agent_steps`` (``kind='steering'``).

    The helper accepts a ``SteeringDecision`` and persists a redacted,
    byte-bounded JSON detail row through the injected audit repository. Without
    a repository it degrades to a no-op so steering remains observable offline.
    """

    def __init__(
        self,
        repo: Any,
        *,
        surface: str = "",
        payload_max_bytes: int = 4 * 1024,
        reason_max_chars: int = 1_000,
    ) -> None:
        self.repo = repo
        self.surface = surface
        self.payload_max_bytes = payload_max_bytes
        self.reason_max_chars = reason_max_chars

    async def record_step(
        self,
        *,
        run_id: str,
        agent_id: str | None,
        node_id: str | None,
        decision: Any,
        tool_name: str | None = None,
        surface: str | None = None,
        policy_version: str = "",
        attempt_summary: dict[str, Any] | None = None,
        decision_source: str = "deterministic",
        outcome: str | None = None,
    ) -> None:
        if self.repo is None:
            return
        detail = _decision_detail(decision, reason_max_chars=self.reason_max_chars)
        if tool_name:
            detail["tool_name"] = tool_name
        detail.update(
            {
                "schema_version": "1",
                "policy_version": policy_version,
                "attempt_summary": attempt_summary or {},
                "decision_source": decision_source,
                "outcome": outcome or str(getattr(decision.kind, "value", "") or ""),
            }
        )
        bounded = redact_value(detail, max_bytes=self.payload_max_bytes)
        if not isinstance(bounded, dict):
            bounded = {"detail": bounded}
        await self.repo.record_step(
            run_id=str(run_id),
            seq=0,
            kind="steering",
            name=f"steering.{getattr(getattr(decision, 'kind', None), 'value', 'decision')}",
            status="completed",
            detail=bounded,
            agent_id=agent_id,
            node_id=node_id,
            surface=self.surface if surface is None else surface,
        )


async def _flush_run(
    repo: Any,
    run_id: str,
    meta: dict[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    try:
        # ensure_run (not start_run) so the started_at/status opened at
        # run_start is not clobbered when the run completes.
        await repo.ensure_run(
            run_id=run_id,
            source=meta.get("source", "github"),
            event_type=meta.get("event_type", "unknown"),
            org_id=meta.get("org_id", ""),
            surface=meta.get("surface", ""),
            workflow_key=meta.get("workflow_key"),
            definition_id=meta.get("definition_id"),
        )
        kind_counters = {
            "node": "draftly_node_steps_total",
            "tool": "draftly_tool_steps_total",
        }
        for step in steps:
            try:
                await repo.record_step(run_id=run_id, **step)
            except TypeError as exc:
                # Third-party/offline repositories may still expose the old
                # write contract; identity fields are additive.
                if "unexpected keyword" not in str(exc):
                    raise
                legacy_step = {key: value for key, value in step.items()
                                if key not in {"agent_id", "node_id", "surface"}}
                await repo.record_step(run_id=run_id, **legacy_step)
            counter = kind_counters.get(str(step.get("kind", "")))
            if counter:
                _metrics.increment(counter)
        failed = [s for s in steps if s["status"] == "failed"]
        await repo.finish_run(
            run_id=run_id,
            status="failed" if failed else "completed",
            error=f"nodes failed: {failed}" if failed else None,
        )
        logger.info(
            "audit_flush_completed",
            run_id=run_id,
            steps=len(steps),
            status="failed" if failed else "completed",
        )
    except Exception:
        logger.exception("audit_flush_failed", run_id=run_id)
        _metrics.increment("draftly_audit_flush_failures_total")


async def _open_run(repo: Any, meta: dict[str, Any]) -> None:
    """Open/reset the run row as ``running`` at invocation start."""
    try:
        await repo.start_run(
            run_id=meta["run_id"],
            source=meta.get("source", "github"),
            event_type=meta.get("event_type", "unknown"),
            org_id=meta.get("org_id", ""),
            surface=meta.get("surface", ""),
            workflow_key=meta.get("workflow_key"),
            definition_id=meta.get("definition_id"),
        )
    except Exception:
        logger.warning("audit_open_run_failed", run_id=meta.get("run_id"), exc_info=True)


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
        logger.warning("audit_stream_flush_failed", run_id=run_id, exc_info=True)


async def _upsert_job_row(
    jobs_repo: Any, *, job_id: str, org_id: str, surface: str, event_type: str
) -> None:
    try:
        await jobs_repo.upsert_on_conflict(
            run_id=job_id,
            org_id=org_id,
            name=surface or event_type or "agent",
            job_type="agent",
            schedule="adhoc",
            configuration={},
            status="running",
        )
    except Exception:
        logger.warning("audit_job_row_upsert_failed", run_id=job_id, exc_info=True)


def _status_of(event: AfterNodeCallEvent) -> str:
    result = getattr(event, "result", None)
    status = getattr(result, "status", None)
    return str(status).rsplit(".", maxsplit=1)[-1].lower() if status else "completed"


def _tool_status_of(event: AfterToolCallEvent) -> str:
    result = getattr(event, "result", None)
    error = getattr(result, "get", lambda *_: None)("status") if result else None
    return "failed" if str(error) == "error" else "completed"
