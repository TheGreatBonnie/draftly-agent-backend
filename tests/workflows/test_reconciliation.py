"""Reconciliation: the events row is the source of truth for read-model statuses."""

from __future__ import annotations

from dataclasses import dataclass, field

from draftly.workflows.context import WorkflowContext
from draftly.workflows.documentation.reconciliation import (
    reconcile_run,
    reconcile_stale_runs,
)


@dataclass
class FakeEvents:
    rows: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    async def find_by_event_id(self, event_id):
        status = self.rows.get(event_id)
        if status is None:
            return None
        return {"event_id": event_id, "status": status}

    async def list_recent_runs(self, *, limit=100):
        return [{"event_id": eid, "status": self.rows[eid]} for eid in self.order[:limit]]


@dataclass
class FakeReadModel:
    updates: list[dict] = field(default_factory=list)
    failing: bool = False

    async def update_status(self, **kwargs):
        if self.failing:
            raise RuntimeError("db down")
        self.updates.append(kwargs)


def make_context(events=None, jobs=None, workflows=None) -> WorkflowContext:
    return WorkflowContext(
        repositories=type(
            "Repos", (), {
                "events": events or FakeEvents(),
                "jobs": jobs or FakeReadModel(),
                "github_workflows": workflows or FakeReadModel(),
            }
        )()
    )


async def test_reconcile_aligns_jobs_and_workflows_from_terminal_event() -> None:
    context = make_context(events=FakeEvents(rows={"evt-1": "completed"}))

    status = await reconcile_run(context, "evt-1")

    assert status == "completed"
    assert context.repositories.jobs.updates == [{"job_id": "evt-1", "status": "completed"}]
    assert context.repositories.github_workflows.updates == [
        {"workflow_id": "evt-1", "status": "completed"}
    ]


async def test_reconcile_noop_for_inflight_or_missing_event() -> None:
    events = FakeEvents(rows={"evt-2": "running"})
    context = make_context(events=events)

    assert await reconcile_run(context, "evt-2") is None
    assert await reconcile_run(context, "evt-missing") is None
    assert context.repositories.jobs.updates == []
    assert context.repositories.github_workflows.updates == []


async def test_reconcile_best_effort_when_job_write_fails() -> None:
    context = make_context(
        events=FakeEvents(rows={"evt-1": "failed"}),
        jobs=FakeReadModel(failing=True),
    )

    status = await reconcile_run(context, "evt-1")

    assert status == "failed"
    assert context.repositories.github_workflows.updates == [
        {"workflow_id": "evt-1", "status": "failed"}
    ]


async def test_sweep_reconciles_only_terminal_recent_runs() -> None:
    events = FakeEvents(
        rows={"a": "completed", "b": "running", "c": "failed"},
        order=["a", "b", "c"],
    )
    context = make_context(events=events)

    count = await reconcile_stale_runs(context, limit=10)

    assert count == 2
    jobs = [u["job_id"] for u in context.repositories.jobs.updates]
    assert jobs == ["a", "c"]
