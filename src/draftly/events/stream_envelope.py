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
