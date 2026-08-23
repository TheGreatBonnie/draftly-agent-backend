"""§10.3 verification — RunAuditLogger writes agent_runs/agent_steps rows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, TypeVar, cast

from strands.hooks import (
    AfterInvocationEvent,
    AfterNodeCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeNodeCallEvent,
)

from draftly.orchestration.hooks.audit import RunAuditLogger

_T = TypeVar("_T")


def _ev(event_type: type[_T], **attrs: Any) -> _T:
    """Duck-typed stand-in for a strands hook event."""
    return cast(_T, SimpleNamespace(**attrs))


@dataclass
class FakeAgentRunsRepo:
    runs: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    finished: list[dict] = field(default_factory=list)

    async def start_run(self, **kwargs):
        self.runs.append(kwargs)

    async def record_step(self, **kwargs):
        self.steps.append(kwargs)

    async def finish_run(self, **kwargs):
        self.finished.append(kwargs)


class TestRunAuditLoggerFlush:
    async def test_graph_run_writes_run_and_step_rows(self) -> None:
        repo = FakeAgentRunsRepo()
        hook = RunAuditLogger(repo)

        state = {
            "run_id": "run-42",
            "source": "github",
            "event_type": "pull_request.opened",
            "project_id": "org-1",
        }
        hook.run_start(_ev(BeforeInvocationEvent, invocation_state=state))

        hook.node_start(_ev(BeforeNodeCallEvent, invocation_state=state, node_id="classify"))
        hook.node_end(
            _ev(
                AfterNodeCallEvent,
                invocation_state=state,
                node_id="classify",
                result=SimpleNamespace(status="COMPLETED"),
            )
        )

        hook.node_start(_ev(BeforeNodeCallEvent, invocation_state=state, node_id="context"))
        hook.node_end(
            _ev(
                AfterNodeCallEvent,
                invocation_state=state,
                node_id="context",
                result=SimpleNamespace(status="COMPLETED"),
            )
        )

        hook.tool_end(
            _ev(
                AfterToolCallEvent,
                invocation_state=state,
                tool_use={"name": "search_docs"},
                result={"status": "success"},
            )
        )

        hook.run_end(_ev(AfterInvocationEvent, invocation_state=state))
        await _await_pending()

        assert len(repo.runs) == 1
        assert repo.runs[0]["run_id"] == "run-42"
        assert repo.runs[0]["org_id"] == "org-1"
        # one row per node + one per tool call
        assert [s["name"] for s in repo.steps] == [
            "classify",
            "context",
            "search_docs",
        ]
        assert repo.steps[0]["kind"] == "node"
        assert repo.steps[2]["kind"] == "tool"
        assert repo.finished[0]["status"] == "completed"

    async def test_failed_node_marks_run_failed(self) -> None:
        repo = FakeAgentRunsRepo()
        hook = RunAuditLogger(repo)
        state = {"run_id": "run-7"}

        hook.node_start(_ev(BeforeNodeCallEvent, invocation_state=state, node_id="evaluate"))
        hook.node_end(
            _ev(
                AfterNodeCallEvent,
                invocation_state=state,
                node_id="evaluate",
                result=SimpleNamespace(status="FAILED"),
            )
        )
        hook.run_end(_ev(AfterInvocationEvent, invocation_state=state))
        await _await_pending()

        assert repo.finished[0]["status"] == "failed"
        assert "evaluate" in repo.finished[0]["error"]

    async def test_flush_failure_never_raises(self) -> None:
        class BrokenRepo:
            async def start_run(self, **kwargs):
                raise RuntimeError("db down")

        hook = RunAuditLogger(BrokenRepo())
        state = {"run_id": "run-9"}
        hook.run_start(_ev(BeforeInvocationEvent, invocation_state=state))
        hook.run_end(_ev(AfterInvocationEvent, invocation_state=state))
        await _await_pending()  # must not raise

    async def test_no_repo_degrades_to_logs(self) -> None:
        hook = RunAuditLogger(None)
        state = {"run_id": "run-11"}
        hook.run_start(_ev(BeforeInvocationEvent, invocation_state=state))
        hook.node_start(_ev(BeforeNodeCallEvent, invocation_state=state, node_id="classify"))
        hook.node_end(
            _ev(
                AfterNodeCallEvent,
                invocation_state=state,
                node_id="classify",
                result=SimpleNamespace(status="COMPLETED"),
            )
        )
        hook.run_end(_ev(AfterInvocationEvent, invocation_state=state))
        await _await_pending()


async def _await_pending() -> None:
    """Await fire-and-forget flush tasks scheduled by run_end."""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        await asyncio.gather(*pending)
