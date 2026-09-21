"""task_progress and documentation.* progress envelope shaping tests."""

from __future__ import annotations

from draftly.events.stream_envelope import (
    DOCUMENTATION_PROGRESS_EVENTS,
    StreamEnvelope,
    documentation_progress_envelope,
    task_progress_envelope,
)


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


def test_documentation_progress_envelope_shape() -> None:
    env = documentation_progress_envelope(
        "documentation.page.evaluated",
        run_id="run-docs-1",
        surface="pull_request",
        node_id="document",
        task_id="evaluate:docs/a.md:2",
        page_id="docs/a.md",
        artifact_version=2,
        attempt=1,
        status="passed",
    )
    assert env.type == "documentation.progress"
    assert env.run_id == "run-docs-1"
    assert env.surface == "pull_request"
    assert env.node_id == "document"
    assert env.payload == {
        "schema_version": "1",
        "event_type": "documentation.page.evaluated",
        "run_id": "run-docs-1",
        "task_id": "evaluate:docs/a.md:2",
        "page_id": "docs/a.md",
        "artifact_version": 2,
        "attempt": 1,
        "status": "passed",
    }


def test_documentation_progress_envelope_round_trips_through_json() -> None:
    env = documentation_progress_envelope(
        "documentation.workflow.resumed",
        run_id="run-docs-2",
        surface="pull_request",
        node_id="document",
        page_id="docs/a.md",
        status="approve",
    )
    restored = StreamEnvelope.from_json(env.to_json())
    assert restored.type == env.type
    assert restored.payload == env.payload


def test_documentation_progress_event_kinds_are_registered() -> None:
    kinds = {
        "documentation.task.claimed",
        "documentation.page.written",
        "documentation.page.evaluated",
        "documentation.page.revision_scheduled",
        "documentation.page.escalated",
        "documentation.workflow.resumed",
    }
    assert kinds <= DOCUMENTATION_PROGRESS_EVENTS
    # The runner routes exactly these to the documentation envelope.
    assert "task_progress" not in DOCUMENTATION_PROGRESS_EVENTS
    assert "documentation.foo" not in DOCUMENTATION_PROGRESS_EVENTS
