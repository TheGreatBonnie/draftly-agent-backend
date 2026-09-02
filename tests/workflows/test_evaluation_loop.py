from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from draftly.workflows.evaluation.documentation_evaluation import run_evaluation_loop


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, envelope: Any) -> None:
        self.events.append(envelope.to_dict())


class FakeBroadcaster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def broadcast(self, org_id: str, event_type: str, payload: dict) -> bool:
        self.calls.append((org_id, event_type, payload))
        return True


class FakeJobs:
    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.statuses: list[tuple[str, str]] = []

    async def insert(self, **kwargs: Any) -> dict:
        row = {"id": "job-1", **kwargs}
        self.inserted.append(row)
        return row

    async def update_status(self, *, job_id: str, status: str) -> dict:
        self.statuses.append((job_id, status))
        return {"id": job_id}


class FakeEvaluations:
    def __init__(self, datasets: list[dict] | None = None) -> None:
        self.datasets = datasets if datasets is not None else [{"name": "geometry", "cases": []}]
        self.saved: list[dict] = []

    def list_datasets(self) -> list[dict]:
        return self.datasets

    async def save_run_summary(self, **kwargs: Any) -> dict | None:
        self.saved.append(kwargs)
        return {"id": "ev-1", "status": "completed"}


def build_context(**overrides: Any) -> Any:
    publisher = overrides.get("publisher", FakePublisher())
    broadcaster = overrides.get("broadcaster", FakeBroadcaster())
    repositories = SimpleNamespace(
        jobs=FakeJobs(),
        evaluations=FakeEvaluations(overrides.get("datasets")),
    )
    return SimpleNamespace(
        repositories=repositories,
        publisher=publisher,
        broadcaster=broadcaster,
        model=None,
        tools=None,
        config=None,
        review_policy=lambda: None,
        graph_limits=lambda: {},
        storage_dir="",
        context=None,
    )


async def test_loop_streams_stage_events_and_result():
    pub = FakePublisher()
    bc = FakeBroadcaster()
    ctx = build_context(publisher=pub, broadcaster=bc)

    state = await run_evaluation_loop(ctx, org_id="org-9", run_id="run-1")

    types = [e["type"] for e in pub.events]
    assert "stage_change" in types
    assert "stage_manifest" in types
    assert "workflow_result" in types
    assert types[-1] == "workflow_result"
    assert pub.events[-1]["payload"]["status"] in ("DELIVERED", "FAILED")

    orgs = [c[0] for c in bc.calls]
    assert "org-9" in orgs
    types_bc = [c[1] for c in bc.calls]
    assert "workflow:changed" in types_bc
    assert "evaluation:created" in types_bc
    assert state.run_id == "run-1"


async def test_loop_persists_summary_and_job_row():
    pub = FakePublisher()
    ctx = build_context(publisher=pub)

    await run_evaluation_loop(ctx, org_id="org-9", run_id="run-1")

    jobs = ctx.repositories.jobs
    assert any(r["run_id"] == "run-1" for r in jobs.inserted)

    evals = ctx.repositories.evaluations
    assert len(evals.saved) == 1
    assert evals.saved[0]["org_id"] == "org-9"
    assert evals.saved[0]["run_id"] == "run-1"
    assert jobs.statuses and jobs.statuses[-1][0] == "run-1"


async def test_loop_publishes_terminal_job_status():
    pub = FakePublisher()
    ctx = build_context(publisher=pub)

    await run_evaluation_loop(ctx, org_id="org-9", run_id="run-1")

    statuses = ctx.repositories.jobs.statuses
    assert statuses and statuses[-1][0] == "run-1"
    assert statuses[-1][1] in ("completed", "failed")
