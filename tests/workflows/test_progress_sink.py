"""Runner progress sink contract tests."""

from __future__ import annotations

from draftly.workflows.runner import _progress_event_sink, _RunStreamSeq


class _RecordingPublisher:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, envelope) -> None:
        self.published.append(envelope)


async def test_progress_sink_publishes_monotonic_task_progress() -> None:
    publisher = _RecordingPublisher()
    seq = _RunStreamSeq()
    sink = _progress_event_sink(
        publisher=publisher, run_id="run-1", surface="pull_request", stream_seq=seq
    )
    await sink(
        {
            "node_id": "document",
            "task_id": "docs/a.md",
            "path": "docs/a.md",
            "action": "update",
            "status": "completed",
            "position": 1,
            "total": 2,
        }
    )
    await sink(
        {
            "node_id": "document",
            "task_id": "docs/b.md",
            "path": "docs/b.md",
            "action": "create",
            "status": "failed",
            "position": 2,
            "total": 2,
        }
    )

    assert [e.type for e in publisher.published] == ["task_progress", "task_progress"]
    assert [e.seq for e in publisher.published] == [1, 2]
    assert publisher.published[0].run_id == "run-1"
    assert publisher.published[1].payload["status"] == "failed"


async def test_progress_sink_survives_publisher_failure() -> None:
    class _BoomPublisher:
        async def publish(self, envelope) -> None:
            raise RuntimeError("down")

    sink = _progress_event_sink(
        publisher=_BoomPublisher(),
        run_id="run-1",
        surface="pull_request",
        stream_seq=_RunStreamSeq(),
    )
    await sink(
        {
            "task_id": "docs/a.md",
            "path": "docs/a.md",
            "status": "running",
            "position": 1,
            "total": 1,
        }
    )  # must not raise
