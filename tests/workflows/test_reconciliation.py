"""Reconciliation: the events row is the source of truth for read-model statuses."""

from __future__ import annotations

from dataclasses import dataclass, field

from draftly.workflows.context import WorkflowContext
from draftly.workflows.documentation.reconciliation import (
    mark_stale_runs_failed,
    reconcile_run,
    reconcile_stale_on_boot,
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


@dataclass
class FakeEventsWithMark(FakeEvents):
    """FakeEvents + a mark_status recorder that flips the in-memory row."""

    marked: list[tuple[str, str]] = field(default_factory=list)

    async def mark_status(self, event_id: str, status: str) -> None:
        self.marked.append((event_id, status))
        self.rows[event_id] = status


@dataclass
class FakeWorkEvents:
    terminal: set[str] = field(default_factory=set)

    async def terminal_run_ids(self, run_ids: list[str]) -> set[str]:
        return self.terminal & set(run_ids)


@dataclass
class FakeJobsRepo:
    """The jobs repo in production has BOTH the sweep lister and the
    reconcile updater (JobRepositoryImpl::list_stuck + update_status). The
    fake mirrors that so reconcile_run can update the same object that
    produced the stuck candidates."""

    rows: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)

    async def list_stuck(self, *, started_before, status="running", limit=500):
        return self.rows

    async def update_status(self, **kwargs):
        self.updates.append(
            {
                "run_id": kwargs.get("job_id") or kwargs.get("workflow_id"),
                "status": kwargs.get("status"),
            }
        )


@dataclass
class FakeBroadcaster:
    sent: list[tuple] = field(default_factory=list)

    async def broadcast(self, org_id, event, payload):
        self.sent.append((org_id, event, payload))


class FakeRepos:
    def __init__(self, events, jobs, workflows, work_events):
        self.events = events
        self.jobs = jobs
        self.github_workflows = workflows
        self.workflow_events = work_events


def make_sweep_context(events=None, jobs=None, workflows=None, work_events=None, broadcaster=None):
    repos = FakeRepos(
        events=events or FakeEvents(rows={}),
        jobs=jobs or FakeJobsRepo(),
        workflows=workflows or FakeReadModel(),
        work_events=work_events or FakeWorkEvents(),
    )
    return WorkflowContext(repositories=repos, broadcaster=broadcaster)


async def test_sweep_fails_stuck_run_through_reconcile() -> None:
    events = FakeEventsWithMark(rows={"stuck-1": "running"})
    jobs = FakeJobsRepo(
        rows=[{"run_id": "stuck-1", "org_id": "org-a", "started_at": None}]
    )
    workflows = FakeReadModel()
    broadcaster = FakeBroadcaster()
    context = make_sweep_context(
        events=events, jobs=jobs, workflows=workflows, work_events=FakeWorkEvents(),
        broadcaster=broadcaster,
    )

    count = await mark_stale_runs_failed(context, stale_after_seconds=1)

    assert count == 1
    assert events.marked == [("stuck-1", "failed")]
    # events row is now terminal, so reconcile_run aligned the read models
    assert jobs.updates == [{"run_id": "stuck-1", "status": "failed"}]
    assert workflows.updates == [{"workflow_id": "stuck-1", "status": "failed"}]
    assert broadcaster.sent == [
        ("org-a", "workflow:changed", {"run_id": "stuck-1", "status": "failed", "kind": "sweep"})
    ]


async def test_sweep_skips_run_with_terminal_result() -> None:
    events = FakeEventsWithMark(rows={"done-1": "running"})
    jobs = FakeJobsRepo(
        rows=[{"run_id": "done-1", "org_id": "org-a", "started_at": None}]
    )
    workflows = FakeReadModel()
    context = make_sweep_context(
        events=events, jobs=jobs, workflows=workflows,
        work_events=FakeWorkEvents(terminal={"done-1"}),
    )

    count = await mark_stale_runs_failed(context, stale_after_seconds=1)

    assert count == 0
    assert events.marked == []
    assert jobs.updates == []
    assert workflows.updates == []


async def test_sweep_noop_when_no_stuck_rows() -> None:
    context = make_sweep_context(jobs=FakeJobsRepo(rows=[]))
    assert await mark_stale_runs_failed(context, stale_after_seconds=1) == 0


async def test_sweep_falls_back_when_events_row_missing() -> None:
    jobs = FakeJobsRepo(
        rows=[{"run_id": "r-1", "org_id": "org-a", "started_at": None}]
    )
    workflows = FakeReadModel()
    context = make_sweep_context(
        events=FakeEvents(rows={}),  # no events row → find_by_event_id → None
        jobs=jobs, workflows=workflows, work_events=FakeWorkEvents(),
    )

    count = await mark_stale_runs_failed(context, stale_after_seconds=1)

    assert count == 1
    assert jobs.updates == [{"run_id": "r-1", "status": "failed"}]
    assert workflows.updates == [{"workflow_id": "r-1", "status": "failed"}]


async def test_boot_helper_extracts_context_from_application() -> None:
    from types import SimpleNamespace

    jobs = FakeJobsRepo(
        rows=[{"run_id": "r-1", "org_id": "org-a", "started_at": None}]
    )
    workflows = FakeReadModel()
    context = make_sweep_context(jobs=jobs, workflows=workflows)
    app = SimpleNamespace(workflows=SimpleNamespace(context=context))

    assert await reconcile_stale_on_boot(app) == 1
    assert jobs.updates == [{"run_id": "r-1", "status": "failed"}]
