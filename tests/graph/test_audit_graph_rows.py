"""§10.3 regression — RunAuditLogger must persist rows for a REAL multi-agent
graph run.

The PR docs workflow drives ``Graph.invoke_async``/``stream_async``, but the
audit logger originally subscribed to the single-agent Invocation events
(``BeforeInvocationEvent``/``AfterInvocationEvent``), which a Strands
multi-agent ``Graph`` never emits. A completed graph run therefore wrote zero
``agent_runs``/``agent_steps`` rows even though node/tool telemetry was
buffered in memory. This test drives the real documentation graph to
completion and asserts the audit rows are flushed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from strands.multiagent.base import Status

from draftly.integrations.strands.graph import build_graph_for_run
from tests.graph.conftest import PR_TASK


@dataclass
class _InMemoryAuditRepo:
    runs: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    finished: list[dict] = field(default_factory=list)

    async def start_run(self, **kwargs):
        self.runs.append({**kwargs, "phase": "start"})

    async def ensure_run(self, **kwargs):
        self.runs.append({**kwargs, "phase": "ensure"})

    async def record_step(self, **kwargs):
        self.steps.append(kwargs)

    async def finish_run(self, **kwargs):
        self.finished.append(kwargs)


async def test_real_multiagent_graph_writes_audit_rows(
    model, tools, tmp_sessions, comment_factory
) -> None:
    factory, _commenter = comment_factory
    repo = _InMemoryAuditRepo()
    run_id = "audit-graph-rows-1"

    graph = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        audit_repo=repo,
        comment_factory=factory,
    )
    assert graph._draftly_audit_hook is not None

    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={
            "run_id": run_id,
            "project_id": "org-1",
            "source": "github",
            "event_type": "pull_request.opened",
            "surface": "pull_request",
            "review_policy": "never",
        },
    )
    assert result.status == Status.COMPLETED

    # Mirror the runner: await the fire-and-forget audit flush so the rows
    # persist before the event loop tears down.
    await graph._draftly_audit_hook.drain_run(run_id)
    await _await_pending_tasks()

    # run_start fires on BeforeMultiAgentInvocationEvent -> the "running"
    # agent_runs row must exist before any step is flushed.
    assert repo.runs, "run_start never fired: agent_runs row was never opened"
    assert repo.runs[0]["phase"] == "start"
    assert repo.runs[0]["run_id"] == run_id
    assert repo.runs[0]["org_id"] == "org-1"
    assert repo.runs[0]["surface"] == "pull_request"

    # run_end fires on AfterMultiAgentInvocationEvent -> buffered node/tool
    # telemetry is flushed as agent_steps plus a final agent_runs ensure row.
    assert [r["phase"] for r in repo.runs].count("ensure") == 1
    assert repo.steps, "run_end never fired: no agent_steps rows were flushed"
    node_names = [s["name"] for s in repo.steps if s["kind"] == "node"]
    assert "classify" in node_names
    assert "deliver" in node_names
    assert all(s["run_id"] == run_id for s in repo.steps)

    assert repo.finished == [
        {"run_id": run_id, "status": "completed", "error": None}
    ]


async def _await_pending_tasks() -> None:
    """Await the fire-and-forget flush tasks scheduled by run_start/run_end."""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        await asyncio.gather(*pending)
