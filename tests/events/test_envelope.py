"""Envelope shaping: raw Strands streaming events -> wire envelopes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from draftly.events.stream_envelope import StreamEnvelope, filter_graph_event

KW = {"run_id": "evt-1", "surface": "documentation"}


def node_start() -> dict[str, Any]:
    return {"type": "multiagent_node_start", "node_id": "writer", "node_type": "agent"}


def node_stream(nested: dict[str, Any]) -> dict[str, Any]:
    return {"type": "multiagent_node_stream", "node_id": "writer", "event": nested}


def node_stop(status: str = "COMPLETED") -> dict[str, Any]:
    return {
        "type": "multiagent_node_stop",
        "node_id": "writer",
        "node_result": {"status": status, "duration": 1.25},
    }


def handoff() -> dict[str, Any]:
    return {
        "type": "multiagent_handoff",
        "from_node_ids": ["classify"],
        "to_node_ids": ["research"],
    }


class TestFilterMapping:
    def test_node_start_maps(self) -> None:
        env = filter_graph_event(node_start(), **KW)
        assert env is not None
        assert env.type == "node_start"
        assert env.node_id == "writer"
        assert env.payload == {"node_type": "agent"}

    def test_nested_text_delta_maps(self) -> None:
        env = filter_graph_event(node_stream({"data": "# Draftly docs"}), **KW)
        assert env is not None
        assert env.type == "text_delta"
        assert env.node_id == "writer"
        assert env.payload == {"text": "# Draftly docs"}

    def test_nested_tool_progress_maps_only_with_name(self) -> None:
        tool = {"current_tool_use": {"name": "search_docs", "toolUseId": "t1", "input": {}}}
        env = filter_graph_event(node_stream(tool), **KW)
        assert env is not None
        assert env.type == "tool_progress"
        assert env.payload["name"] == "search_docs"

        unnamed = node_stream({"current_tool_use": {"input": {}}})
        assert filter_graph_event(unnamed, **KW) is None

    def test_node_stop_extracts_status_and_duration(self) -> None:
        env = filter_graph_event(node_stop(), **KW)
        assert env is not None
        assert env.type == "node_stop"
        assert env.payload == {"status": "COMPLETED", "duration_ms": 1250}

    def test_handoff_maps(self) -> None:
        env = filter_graph_event(handoff(), **KW)
        assert env is not None
        assert env.type == "handoff"
        assert env.payload == {"from": ["classify"], "to": ["research"]}


class TestDroppedAndTerminal:
    @pytest.mark.parametrize(
        "raw",
        [
            {"init_event_loop": True},
            {"start_event_loop": True},
            {"delta": "raw"},
            {"reasoning": True, "reasoningText": "thinking"},
            {"message": {"role": "assistant"}},
            {},
        ],
    )
    def test_noise_is_dropped(self, raw: dict[str, Any]) -> None:
        assert filter_graph_event(raw, **KW) is None

    def test_force_stop_without_result_maps_terminal_error(self) -> None:
        raw = {"force_stop": True, "force_stop_reason": "max_iterations"}
        env = filter_graph_event(raw, **KW)
        assert env is not None
        assert env.type == "workflow_result"
        assert env.payload["status"] == "FAILED"
        assert env.payload["force_stop_reason"] == "max_iterations"

    def test_result_event_carries_status_and_interrupts(self) -> None:
        interrupt = type("Interrupt", (), {"id": "i-1", "reason": {"summary": "review"}})()
        result = type(
            "GraphResult",
            (),
            {"status": type("Status", (), {"name": "INTERRUPTED"})(), "interrupts": [interrupt]},
        )()
        raw = {"result": result}
        env = filter_graph_event(raw, **KW)
        assert env is not None
        assert env.type == "workflow_result"
        assert env.payload["status"] == "INTERRUPTED"
        assert env.payload["interrupts"] == [{"id": "i-1", "reason": {"summary": "review"}}]
        # no metrics on this double -> token keys omitted entirely
        assert "tokens_in" not in env.payload

    def test_result_event_includes_token_usage_when_present(self) -> None:
        result = type(
            "GraphResult",
            (),
            {
                "status": type("Status", (), {"name": "COMPLETED"})(),
                "interrupts": [],
                "metrics": SimpleNamespace(
                    accumulated_usage={"inputTokens": 1200, "outputTokens": 340}
                ),
            },
        )()
        env = filter_graph_event({"result": result}, **KW)
        assert env is not None
        assert env.payload["tokens_in"] == 1200
        assert env.payload["tokens_out"] == 340


class TestEnvelopeShape:
    def test_envelope_json_round_trip(self) -> None:
        env = StreamEnvelope(type="node_start", run_id="r", surface="documentation")
        env.payload = {"node_type": "agent"}
        decoded = json.loads(json.dumps(env.to_dict()))
        assert decoded["type"] == "node_start"
        assert decoded["run_id"] == "r"
        assert decoded["seq"] == 0
        assert decoded["ts"]

    def test_non_dict_event_is_dropped(self) -> None:
        assert filter_graph_event("garbage", **KW) is None  # type: ignore[arg-type]
