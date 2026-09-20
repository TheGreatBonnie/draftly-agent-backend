"""task_progress envelope shaping tests."""

from __future__ import annotations

from draftly.events.stream_envelope import StreamEnvelope, task_progress_envelope


def test_task_progress_envelope_shape() -> None:
    env = task_progress_envelope(
        run_id="run-1",
        surface="pull_request",
        node_id="document",
        task_id="docs/a.md",
        path="docs/a.md",
        action="update",
        status="running",
        position=1,
        total=3,
    )
    assert env.type == "task_progress"
    assert env.run_id == "run-1"
    assert env.surface == "pull_request"
    assert env.node_id == "document"
    assert env.payload == {
        "schema_version": "1",
        "task_id": "docs/a.md",
        "path": "docs/a.md",
        "action": "update",
        "status": "running",
        "position": 1,
        "total": 3,
    }


def test_task_progress_envelope_round_trips_through_json() -> None:
    env = task_progress_envelope(
        run_id="run-1",
        surface="pull_request",
        node_id="document",
        task_id="docs/b.md",
        path="docs/b.md",
        action="create",
        status="completed",
        position=2,
        total=2,
    )
    restored = StreamEnvelope.from_json(env.to_json())
    assert restored.type == env.type
    assert restored.payload == env.payload


def test_task_progress_envelope_defaults() -> None:
    env = task_progress_envelope(task_id="t", path="p")
    assert env.type == "task_progress"
    assert env.run_id == ""
    assert env.seq == 0
