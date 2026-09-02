"""Audit hook publishes per-step SSE envelopes via its publisher."""

from __future__ import annotations

from types import SimpleNamespace
from draftly.orchestration.hooks.audit import RunAuditLogger


class RecordingPublisher:
    def __init__(self) -> None:
        self.envelopes: list = []

    async def publish(self, envelope) -> bool:
        self.envelopes.append(envelope)
        return True


def _event(node_id="doc_writer"):
    return SimpleNamespace(
        node_id=node_id,
        invocation_state={"run_id": "evt-1", "project_id": "org-1", "source": "github"},
        result=SimpleNamespace(status="COMPLETED"),
    )


def test_run_end_publishes_step_envelopes() -> None:
    pub = RecordingPublisher()
    logger = RunAuditLogger(audit_repo=None, publisher=pub)
    logger.node_start(_event())
    logger.node_end(_event())
    import asyncio
    asyncio.run(logger.run_end_async(_event()))
    types = {e.type for e in pub.envelopes}
    assert {"node_start", "node_stop"} <= types
    assert all(e.run_id == "evt-1" for e in pub.envelopes)
