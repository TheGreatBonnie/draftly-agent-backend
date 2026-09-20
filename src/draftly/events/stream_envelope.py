"""Wire-envelope shaping for Strands graph streams (spec §Wire contract).

``filter_graph_event`` is the single place that knows how raw Strands
streaming dicts map onto Draftly's compact SSE envelopes; everything else
consumes ``StreamEnvelope``. Pure module: no I/O, fully table-testable.
Distinct from ``events.envelope`` (webhook normalization, plan §7.1).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from draftly.steering.redaction import redact_value, scrub_secret_values

EnvelopeType = str  # Literal set documented on StreamEnvelope.type


@dataclass(slots=True)
class StreamEnvelope:
    """One filtered graph event, ready for JSON serialization."""

    type: str
    run_id: str
    surface: str
    seq: int = 0
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    node_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "run_id": self.run_id,
            "surface": self.surface,
            "seq": self.seq,
            "ts": self.ts,
            "node_id": self.node_id,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_json(cls, raw: str) -> StreamEnvelope:
        data = json.loads(raw)
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def steering_envelope(
    decision: Any,
    *,
    run_id: str = "",
    surface: str = "",
    node_id: str | None = None,
    agent_id: str | None = None,
    tool_name: str | None = None,
    attempt_summary: Mapping[str, Any] | None = None,
    payload_max_bytes: int = 4 * 1024,
) -> StreamEnvelope:
    """Shape one redacted, byte-bounded steering decision.

    Only safe fields are emitted: ``schema_version``, ``phase``, ``action``,
    ``role``, safe agent/node/tool IDs, reason/rule source, an optional attempt
    summary, and ``interrupt_id`` when present. Actor-supplied tool arguments
    and model messages never appear; the reason string is value-scrubbed for
    credentials and the whole payload is bounded via ``redact_value``.
    """
    role = getattr(decision, "role", None)
    payload: dict[str, Any] = {
        "schema_version": "1",
        "phase": str(getattr(getattr(decision, "phase", None), "value", "") or ""),
        "action": str(getattr(getattr(decision, "kind", None), "value", "") or ""),
        "role": str(getattr(role, "value", "") or "") if role else None,
        "rule": str(getattr(decision, "rule", "") or ""),
        "reason": scrub_secret_values(str(getattr(decision, "reason", "") or "")),
        "agent_id": agent_id,
        "node_id": node_id,
        "tool_name": tool_name,
        "interrupt_id": getattr(decision, "interrupt_id", None),
    }
    if attempt_summary is not None:
        payload["attempt_summary"] = dict(attempt_summary)
    bounded = redact_value(payload, max_bytes=payload_max_bytes)
    return StreamEnvelope(
        type="steering",
        run_id=run_id,
        surface=surface,
        node_id=node_id,
        payload=bounded,
    )


def task_progress_envelope(
    *,
    run_id: str = "",
    surface: str = "",
    node_id: str | None = None,
    task_id: str = "",
    path: str = "",
    action: str = "",
    status: str = "",
    position: int = 0,
    total: int = 0,
) -> StreamEnvelope:
    """Shape one per-task progress envelope from the fan-out writer node.

    Safe fields only (task/path/action/status/position/total); never carries
    draft content or model messages. Consumed by `filter_graph_event`-free
    out-of-band publishing (see runner progress sink).
    """
    return StreamEnvelope(
        type="task_progress",
        run_id=run_id,
        surface=surface,
        node_id=node_id,
        payload={
            "schema_version": "1",
            "task_id": task_id,
            "path": path,
            "action": action,
            "status": status,
            "position": position,
            "total": total,
        },
    )


def _status_name(result: Any) -> str:
    status = getattr(result, "status", None)
    name = getattr(status, "name", None)
    if name:
        return str(name)
    return str(status).rsplit(".", maxsplit=1)[-1].upper() if status else "UNKNOWN"


def _interrupt_records(result: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for interrupt in getattr(result, "interrupts", None) or []:
        records.append(
            {
                "id": str(getattr(interrupt, "id", "")),
                "reason": getattr(interrupt, "reason", None),
            }
        )
    return records


def _token_usage(result: Any) -> dict[str, int] | None:
    """Per-run token totals from EventLoopMetrics; None when unavailable."""
    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None)
    if not isinstance(usage, dict):
        return None
    return {
        "tokens_in": int(usage.get("inputTokens") or 0),
        "tokens_out": int(usage.get("outputTokens") or 0),
    }


def filter_graph_event(
    event: Mapping[str, Any],
    *,
    run_id: str,
    surface: str,
) -> StreamEnvelope | None:
    """Map one raw Strands event to an envelope, or None to drop it."""

    if not isinstance(event, Mapping):
        return None

    kind = str(event.get("type", ""))
    node_id = event.get("node_id")
    node_str = str(node_id) if node_id is not None else None

    if kind == "multiagent_node_start":
        return StreamEnvelope(
            type="node_start",
            run_id=run_id,
            surface=surface,
            node_id=node_str,
            payload={"node_type": str(event.get("node_type", "agent"))},
        )

    if kind == "multiagent_node_stream":
        nested = _as_dict(event.get("event"))
        if isinstance(nested.get("data"), str):
            return StreamEnvelope(
                type="text_delta",
                run_id=run_id,
                surface=surface,
                node_id=node_str,
                payload={"text": nested["data"]},
            )
        tool = _as_dict(nested.get("current_tool_use"))
        if tool.get("name"):
            return StreamEnvelope(
                type="tool_progress",
                run_id=run_id,
                surface=surface,
                node_id=node_str,
                payload={
                    "name": str(tool["name"]),
                    "tool_use_id": str(tool.get("toolUseId", "")),
                },
            )
        return None

    if kind == "multiagent_node_stop":
        node_result = _as_dict(event.get("node_result"))
        duration = node_result.get("duration")
        duration_ms = int(float(duration) * 1000) if duration is not None else None
        return StreamEnvelope(
            type="node_stop",
            run_id=run_id,
            surface=surface,
            node_id=node_str,
            payload={
                "status": str(node_result.get("status", "UNKNOWN")),
                "duration_ms": duration_ms,
            },
        )

    if kind == "multiagent_handoff":
        return StreamEnvelope(
            type="handoff",
            run_id=run_id,
            surface=surface,
            payload={
                "from": [str(n) for n in event.get("from_node_ids", [])],
                "to": [str(n) for n in event.get("to_node_ids", [])],
            },
        )

    if "result" in event:
        result = event["result"]
        payload: dict[str, Any] = {
            "status": _status_name(result),
            "interrupts": _interrupt_records(result),
        }
        usage = _token_usage(result)
        if usage is not None:
            payload.update(usage)
        return StreamEnvelope(
            type="workflow_result",
            run_id=run_id,
            surface=surface,
            payload=payload,
        )

    if event.get("force_stop"):
        return StreamEnvelope(
            type="workflow_result",
            run_id=run_id,
            surface=surface,
            payload={
                "status": "FAILED",
                "force_stop_reason": str(event.get("force_stop_reason", "unknown")),
                "interrupts": [],
            },
        )

    return None
