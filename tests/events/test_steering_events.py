"""Steering audit-event shaping: redaction, bounds, identity, ordering."""

from __future__ import annotations

import json

from draftly.events.stream_envelope import StreamEnvelope, steering_envelope
from draftly.steering.decisions import (
    AgentRole,
    DecisionKind,
    SteeringDecision,
    SteeringPhase,
)


def safe_decision() -> SteeringDecision:
    return SteeringDecision.proceed(
        phase=SteeringPhase.BEFORE_TOOL,
        reason="deterministic policy ok",
        role=AgentRole.WRITER,
        rule="policy:ok",
    )


def test_steering_event_contains_only_redacted_bounded_fields() -> None:
    decision = SteeringDecision.interrupt(
        phase=SteeringPhase.BEFORE_TOOL,
        reason="credentials token=sk-abcd1234EFGH5678 unsafe destination",
        role=AgentRole.DELIVERY,
        rule="delivery:destination",
    )
    envelope = steering_envelope(decision, payload_max_bytes=4096)
    assert envelope.type == "steering"
    dumped = json.dumps(envelope.payload)
    assert "sk-abcd1234EFGH5678" not in dumped
    assert len(json.dumps(envelope.payload).encode()) <= 4096


def test_steering_event_carries_typed_fields_and_identity() -> None:
    envelope = steering_envelope(
        safe_decision(),
        run_id="run-9",
        surface="documentation",
        node_id="writer",
        agent_id="agent-w",
        tool_name="write_file",
    )
    payload = envelope.payload
    assert envelope.type == "steering"
    assert envelope.run_id == "run-9"
    assert envelope.surface == "documentation"
    assert envelope.node_id == "writer"
    assert payload["schema_version"] == "1"
    assert payload["phase"] == "before_tool"
    assert payload["action"] == "proceed"
    assert payload["role"] == "writer"
    assert payload["rule"] == "policy:ok"
    assert payload["agent_id"] == "agent-w"
    assert payload["tool_name"] == "write_file"
    # no actor-supplied tool arguments or model messages are emitted
    assert "content" not in payload
    assert "tool_use" not in payload


def test_steering_event_includes_interrupt_id_when_present() -> None:
    decision = SteeringDecision(
        kind=DecisionKind.INTERRUPT,
        phase=SteeringPhase.BEFORE_TOOL,
        reason="halt",
        role=AgentRole.DELIVERY,
        rule="delivery:idempotency",
        interrupt_id="i-42",
    )
    envelope = steering_envelope(decision)
    assert envelope.payload["interrupt_id"] == "i-42"


def test_steering_event_omits_interrupt_id_when_absent() -> None:
    envelope = steering_envelope(safe_decision())
    assert envelope.payload.get("interrupt_id") is None


def test_steering_event_payload_is_bounded_when_reason_is_large() -> None:
    decision = SteeringDecision.interrupt(
        phase=SteeringPhase.BEFORE_TOOL,
        reason="x" * 50_000,
        role=AgentRole.DELIVERY,
        rule="delivery:destination",
    )
    envelope = steering_envelope(decision, payload_max_bytes=1024)
    assert len(json.dumps(envelope.payload).encode()) <= 1024
    assert "x" * 50_000 not in json.dumps(envelope.payload)


def test_steering_event_includes_attempt_summary_when_provided() -> None:
    envelope = steering_envelope(
        safe_decision(),
        attempt_summary={
            "tool_guides_per_call": 4,
            "model_guides_per_turn": 2,
            "total_guides_per_agent": 7,
        },
    )
    assert envelope.payload["attempt_summary"] == {
        "tool_guides_per_call": 4,
        "model_guides_per_turn": 2,
        "total_guides_per_agent": 7,
    }


def test_steering_event_json_round_trip_via_stream_envelope() -> None:
    envelope = steering_envelope(
        safe_decision(),
        run_id="run-9",
        surface="docs",
        node_id="writer",
        agent_id="a1",
        tool_name="write_file",
    )
    envelope.seq = 3
    decoded = StreamEnvelope.from_json(envelope.to_json())
    assert decoded.type == "steering"
    assert decoded.seq == 3
    assert decoded.payload["agent_id"] == "a1"
    assert decoded.payload["rule"] == "policy:ok"


def test_shared_allocator_assigns_unique_per_run_sequences() -> None:
    """The streaming loop and steering sink share one monotonic allocator."""
    from draftly.workflows.runner import _RunStreamSeq

    first = _RunStreamSeq()
    second = _RunStreamSeq()
    assert first.next() == 1
    assert first.next() == 2
    assert second.next() == 1  # per-run isolation
