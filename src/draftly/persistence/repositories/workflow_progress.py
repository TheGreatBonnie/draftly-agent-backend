"""Display-safe progress projection from durable workflow node events."""

from __future__ import annotations

import json
from typing import Any


def project_progress(events: list[dict[str, Any]]) -> dict[str, Any]:
    states: dict[str, str] = {}
    sequence: list[str] = []
    current: str | None = None
    for event in sorted(events, key=lambda item: int(item.get("seq") or 0)):
        kind = event.get("type")
        node = event.get("node_id")
        if kind not in {"node_start", "node_stop"} or not node:
            continue
        node = str(node)
        if node not in states:
            sequence.append(node)
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        states[node] = (
            "running" if kind == "node_start" else
            "completed" if payload.get("status") == "COMPLETED" else "failed"
        )
        current = node
    return {"current_stage": current, "stage_states": states, "stage_sequence": sequence}
