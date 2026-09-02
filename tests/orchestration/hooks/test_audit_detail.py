"""Audit hook writes agent/tool detail into step records."""

from __future__ import annotations

from draftly.orchestration.hooks.audit import RunAuditLogger


class RecordingRepo:
    def __init__(self) -> None:
        self.steps: list[dict] = []

    async def start_run(self, **kw) -> None:
        pass

    async def record_step(self, run_id=None, seq=0, kind="", name="",
                          status="", duration_ms=None, detail=None) -> None:
        self.steps.append(
            {"kind": kind, "name": name, "status": status, "detail": detail}
        )

    async def finish_run(self, **kw) -> None:
        pass


def _bump(logger: RunAuditLogger) -> None:
    logger._buffer_step(
        run_id="evt-1",
        kind="node",
        name="doc_writer",
        status="completed",
        duration_ms=120,
        detail={"agent_name": "doc_writer", "capabilities": ["write", "format"]},
    )


def test_buffer_step_records_detail() -> None:
    logger = RunAuditLogger()
    _bump(logger)
    step = logger._steps[0]
    assert step["detail"] == {
        "agent_name": "doc_writer",
        "capabilities": ["write", "format"],
    }
