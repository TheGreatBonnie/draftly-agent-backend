"""§Task 7 of the agent-steering plan: PENDING_INTERVENTION lifecycle.

Covers the steering-intervention branch of the runner: an INTERRUPTED run whose
interrupt has a durable ``workflow_interventions`` row transitions to
``pending_intervention`` (never PENDING_REVIEW), and
:meth:`WorkflowRunner.resume_intervention` claims the row atomically, resumes the
interrupted Strands session with the exact ``interruptResponse``, and routes the
outcome back through the standard lifecycle. Review-gate interrupts must keep
their existing PENDING_REVIEW behavior even when a steering repo is wired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from strands.interrupt import Interrupt
from strands.multiagent.base import MultiAgentResult, Status

from draftly.persistence.repositories.steering import (
    InterventionConflictError,
    InterventionNotFoundError,
    InterventionRecord,
    InvalidInterventionActionError,
)
from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import InterventionResumeError, WorkflowRunner
from draftly.workflows.state import WorkflowState, WorkflowStatus

# ================================================================
# Fakes: in-memory steering-intervention repo + a graph that
# interrupts once and then completes after the resume payload.
# ================================================================


@dataclass
class FakeEventsRepo:
    claimed: dict = field(default_factory=dict)
    statuses: dict = field(default_factory=dict)

    async def try_claim(self, event_id, **kwargs):
        if event_id in self.claimed:
            return False
        self.claimed[event_id] = kwargs
        return True

    async def find_by_event_id(self, event_id):
        if event_id not in self.claimed:
            return None
        return {"event_id": event_id, "status": self.statuses.get(event_id, "running")}

    async def mark_status(self, event_id, status):
        self.statuses[event_id] = status


@dataclass
class FakeReviewsRepo:
    interrupts: list = field(default_factory=list)

    async def store_interrupt(self, **kwargs):
        self.interrupts.append(kwargs)
        return {"id": f"review-{len(self.interrupts)}"}


@dataclass
class FakeJobsRepo:
    statuses: list[dict] = field(default_factory=list)

    async def update_status(self, **kwargs):
        self.statuses.append(kwargs)
        return kwargs


@dataclass
class FakeGitHubWorkflowsRepo:
    statuses: list[dict] = field(default_factory=list)

    async def update_status(self, **kwargs):
        self.statuses.append(kwargs)


@dataclass
class FakeDeliveryRepo:
    pull_requests: list = field(default_factory=list)

    async def save_pull_request(self, pull_request):
        self.pull_requests.append(pull_request)
        return pull_request


@dataclass
class FakeDocumentsRepo:
    upserts: list = field(default_factory=list)

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        return kwargs


_ACTION_STATUS = {
    "approve": "approved",
    "approve_and_review": "approved",
    "deny": "denied",
    "denied": "denied",
    "guide": "guided",
    "cancel": "cancelled",
}


@dataclass
class FakeSteeringInterventionsRepo:
    """Single-writer in-memory view of ``workflow_interventions``.

    ``claim_response`` mirrors the real repo: idempotent replays by
    (org, idempotency_key) conflict when the response differs, and resolving a
    pending row raises when nothing is pending anymore.
    """

    pending: dict = field(default_factory=dict)
    resolved: list[InterventionRecord] = field(default_factory=list)

    def seed(self, record: InterventionRecord) -> None:
        assert record.status == "pending"
        self.pending[(record.run_id, record.interrupt_id)] = record

    async def create_pending(self, *, record: InterventionRecord) -> InterventionRecord:
        key = (record.run_id, record.interrupt_id)
        existing = self.pending.get(key)
        if existing is not None:
            return existing
        record.id = record.id or f"i-{len(self.resolved) + 1}"
        self.pending[key] = record
        return record

    async def get_pending(
        self, *, run_id: str, interrupt_id: str, org_id: str
    ) -> InterventionRecord | None:
        record = self.pending.get((run_id, interrupt_id))
        if record is None or record.status != "pending" or record.org_id != org_id:
            return None
        return record

    async def claim_response(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        org_id: str,
        idempotency_key: str,
        action: str,
        message: str | None,
    ) -> InterventionRecord:
        status = _ACTION_STATUS.get(action)
        if status is None:
            raise InvalidInterventionActionError(f"unsupported claim action '{action}'")
        for record in self.resolved:
            if record.org_id != org_id or record.idempotency_key != idempotency_key:
                continue
            if record.resolver_id != action or record.response_message != message:
                raise InterventionConflictError(
                    f"intervention {record.id} already resolved with a different response"
                )
            return record
        record = self.pending.get((run_id, interrupt_id))
        if record is None or record.status != "pending" or record.org_id != org_id:
            raise InterventionNotFoundError(
                f"no pending intervention for run {run_id} interrupt {interrupt_id}"
            )
        record.status = status
        record.resolver_id = action
        record.response_message = message
        record.idempotency_key = idempotency_key
        del self.pending[(run_id, interrupt_id)]
        self.resolved.append(record)
        return record

    async def resolve(
        self, *, intervention_id: str, status: str, resolver_id: str | None = None
    ) -> InterventionRecord:
        for key, record in list(self.pending.items()):
            if record.id != intervention_id:
                continue
            record.status = status
            record.resolver_id = resolver_id or record.resolver_id
            del self.pending[key]
            self.resolved.append(record)
            return record
        raise InterventionNotFoundError(f"no pending intervention with id {intervention_id}")


class DeliberatingGraph:
    """Interrupt once (steering), then complete after the resume payload."""

    _resume_from_session = True

    def __init__(
        self,
        *,
        interrupt_id: str = "steer-int-1",
        interrupt_name: str = "steering_input_commit_changes",
        resumable: bool = True,
        resume_rejects: bool = False,
    ):
        self.id = "fake-graph"
        self.interrupt_id = interrupt_id
        self.interrupt_name = interrupt_name
        self.resumable = resumable
        self.resume_rejects = resume_rejects
        self.interrupted = False
        self.calls: list[dict[str, Any]] = []
        self.responses: list[Any] = []
        self._interrupt_state = SimpleNamespace(
            activated=True, interrupts={interrupt_id}
        )
        if resumable:
            self.session_manager = SimpleNamespace(
                _is_new_session=False,
                session_id="sess",
                session_repository=SimpleNamespace(
                    read_multi_agent=lambda session_id, graph_id: {
                        "next_nodes_to_execute": ["commit"]
                    }
                ),
            )
        else:
            self.session_manager = SimpleNamespace(
                _is_new_session=True,
                session_id="sess",
                session_repository=None,
            )

    async def invoke_async(self, task, invocation_state=None, **kwargs):
        del kwargs
        self.calls.append({"task": task, "invocation_state": invocation_state})
        if not self.interrupted:
            self.interrupted = True
            result = MultiAgentResult(status=Status.INTERRUPTED)
            result.interrupts = [
                Interrupt(
                    id=self.interrupt_id,
                    name=self.interrupt_name,
                    reason={"message": "steering pause"},
                )
            ]
            return result
        self.responses.append(task)
        if self.resume_rejects:
            return failed_result()
        return completed_result()


def review_interrupt_result() -> MultiAgentResult:
    result = MultiAgentResult(status=Status.INTERRUPTED)
    result.interrupts = [
        Interrupt(
            id="v1:before_node_call:deliver:doc-review",
            name="doc-review",
            reason={"summary": "docs update"},
        )
    ]
    return result


def completed_result() -> MultiAgentResult:
    return MultiAgentResult(status=Status.COMPLETED)


def failed_result() -> MultiAgentResult:
    return MultiAgentResult(status=Status.FAILED)


def event_for(run_id: str) -> dict[str, Any]:
    return {
        "event_id": run_id,
        "event_type": "pull_request.opened",
        "project_id": "org-1",
        "repository": "acme/docs",
        "actor": "dev",
        "source": "github",
        "pull_request": {"number": 7},
    }


def make_context(
    *, pending: list[InterventionRecord] | None = None
) -> tuple[WorkflowContext, FakeSteeringInterventionsRepo]:
    interventions = FakeSteeringInterventionsRepo()
    for record in pending or []:
        interventions.seed(record)
    repositories = type(
        "Repos",
        (),
        {
            "events": FakeEventsRepo(),
            "reviews": FakeReviewsRepo(),
            "jobs": FakeJobsRepo(),
            "github_workflows": FakeGitHubWorkflowsRepo(),
            "delivery": FakeDeliveryRepo(),
            "documents": FakeDocumentsRepo(),
            "steering_interventions": interventions,
        },
    )()
    context = WorkflowContext(
        repositories=repositories,
        config=type("Config", (), {"strands": None})(),
    )
    return context, interventions


class GraphHarness:
    """Serves one fresh ``DeliberatingGraph`` per factory call and records the
    resume payloads so tests can assert the exact interrupt response."""

    def __init__(self, *, graph_cls=DeliberatingGraph, **graph_kwargs):
        self.graph_cls = graph_cls
        self.graph_kwargs = graph_kwargs
        self.graphs: list[DeliberatingGraph] = []

    def factory(self, run_id: str, surface: str):
        del run_id, surface
        graph = self.graph_cls(**self.graph_kwargs)
        if self.graphs:
            # Any graph served after the initial run is a resume graph: it must
            # answer the interruptResponse directly instead of pausing again.
            graph.interrupted = True
        self.graphs.append(graph)
        return graph

    def resume_invokes(self) -> list[Any]:
        return [task for graph in self.graphs for task in graph.responses]

    def assert_called_with_interrupt_response(
        self, interrupt_id: str, *, approved: bool
    ) -> None:
        for task in self.resume_invokes():
            for block in task if isinstance(task, list) else [task]:
                if not isinstance(block, dict):
                    continue
                response = block.get("interruptResponse")
                if not isinstance(response, dict) or response.get("interruptId") != interrupt_id:
                    continue
                inner = response.get("response")
                if isinstance(inner, dict) and inner.get("approved") is approved:
                    return
        raise AssertionError(
            f"graph never received interruptResponse id={interrupt_id} approved={approved}"
        )


def pending_intervention(
    *, run_id: str = "run-1", interrupt_id: str = "steer-int-1"
) -> InterventionRecord:
    return InterventionRecord(
        id=f"i-{interrupt_id}",
        run_id=run_id,
        interrupt_id=interrupt_id,
        org_id="org-1",
        surface="documentation",
        workflow_key="github_pr",
        agent_id="documentation.reviewer",
        node_id="commit",
        tool_name="github_commit_changes",
        status="pending",
        reason={"rule": "side_effect", "action": "interrupt", "phase": "deliver"},
        metadata={},
        idempotency_key=f"org-1:{interrupt_id}",
    )


async def make_through_pause(
    harness: GraphHarness, *, run_id: str = "run-1"
) -> tuple[WorkflowState, WorkflowContext, FakeSteeringInterventionsRepo]:
    """Run an event to PENDING_INTERVENTION against a PENDING seeded row."""
    context, interventions = make_context(pending=[pending_intervention(run_id=run_id)])
    runner = WorkflowRunner(context, graph_factory=harness.factory)
    state = await runner.run(event_for(run_id))
    return state, context, interventions


def resume_runner(context: WorkflowContext, harness: GraphHarness) -> WorkflowRunner:
    return WorkflowRunner(context, graph_factory=harness.factory)


# ================================================================
# Tests: run() → PENDING_INTERVENTION
# ================================================================


async def test_steering_interrupt_enters_pending_intervention() -> None:
    harness = GraphHarness()
    state, context, interventions = await make_through_pause(harness)

    assert state.status is WorkflowStatus.PENDING_INTERVENTION
    assert state.interrupts and state.interrupts[0]["interrupt_id"] == "steer-int-1"
    assert await interventions.get_pending(
        run_id="run-1", interrupt_id="steer-int-1", org_id="org-1"
    ) is not None
    assert context.events.statuses["run-1"] == "pending_intervention"
    assert not context.reviews.interrupts, "steering interrupts must not enter the review inbox"


async def test_steering_interrupt_never_notifies_reviewers() -> None:
    notified: list[str] = []

    class Notifier:
        async def notify_reviewers(self, run_id: str) -> dict:
            notified.append(run_id)
            return {}

    context, _ = make_context(pending=[pending_intervention()])
    context.notifier = Notifier()
    runner = WorkflowRunner(
        context,
        graph_factory=GraphHarness().factory,
    )
    state = await runner.run(event_for("run-1"))

    assert state.status is WorkflowStatus.PENDING_INTERVENTION
    assert notified == []


async def test_review_interrupt_stays_pending_review_when_steering_repo_is_wired() -> None:
    context, interventions = make_context()
    graph = SimpleNamespace()
    graph.id = "fake-graph"
    graph._interrupt_state = SimpleNamespace(
        activated=True, interrupts={"v1:before_node_call:deliver:doc-review"}
    )
    graph.session_manager = SimpleNamespace(
        _is_new_session=False,
        session_id="sess",
        session_repository=SimpleNamespace(
            read_multi_agent=lambda session_id, graph_id: {
                "next_nodes_to_execute": ["review"]
            }
        ),
    )

    async def invoke(self, task, invocation_state=None, **kwargs):
        del task, invocation_state, kwargs
        return review_interrupt_result()

    graph.invoke_async = invoke.__get__(graph, SimpleNamespace)
    runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: graph)

    state = await runner.run(event_for("run-1"))

    assert state.status is WorkflowStatus.PENDING_REVIEW
    assert context.reviews.interrupts, "review interrupt still lands in the review inbox"
    assert not interventions.resolved


# ================================================================
# Tests: resume_intervention()
# ================================================================


async def test_resume_intervention_uses_exact_interrupt_and_is_idempotent() -> None:
    harness = GraphHarness()
    state, context, interventions = await make_through_pause(harness)
    assert state.status is WorkflowStatus.PENDING_INTERVENTION

    runner = resume_runner(context, harness)
    resumed = await runner.resume_intervention(
        event=event_for("run-1"),
        interrupt_id="steer-int-1",
        response={"action": "approve", "message": "validated"},
    )

    assert resumed.status in {WorkflowStatus.DELIVERED, WorkflowStatus.FAILED}
    harness.assert_called_with_interrupt_response("steer-int-1", approved=True)
    assert context.events.statuses["run-1"] == "completed"
    resolved = [r for r in interventions.resolved if r.id == "i-steer-int-1"]
    assert resolved and resolved[0].status == "approved"

    runner = resume_runner(context, harness)
    again = await runner.resume_intervention(
        event=event_for("run-1"),
        interrupt_id="steer-int-1",
        response={"action": "approve", "message": "validated"},
    )
    assert again.status == resumed.status
    assert len(harness.graphs) == 2, "idempotent replay must not invoke the graph again"


async def test_resume_intervention_deny_maps_to_approved_false() -> None:
    harness = GraphHarness()
    state, context, interventions = await make_through_pause(harness)
    assert state.status is WorkflowStatus.PENDING_INTERVENTION

    runner = resume_runner(context, harness)
    resumed = await runner.resume_intervention(
        event=event_for("run-1"),
        interrupt_id="steer-int-1",
        response={"action": "deny", "message": "not now"},
    )

    harness.assert_called_with_interrupt_response("steer-int-1", approved=False)
    resolved = [r for r in interventions.resolved if r.id == "i-steer-int-1"]
    assert resolved and resolved[0].status == "denied"
    assert resolved[0].response_message == "not now"
    assert resumed.status in {WorkflowStatus.DELIVERED, WorkflowStatus.FAILED}


async def test_resume_intervention_unknown_interrupt_raises_without_invoke() -> None:
    harness = GraphHarness()
    context, _ = make_context()
    runner = resume_runner(context, harness)
    event = event_for("run-9")

    with pytest.raises(InterventionResumeError):
        await runner.resume_intervention(
            event=event,
            interrupt_id="steer-missing",
            response={"action": "approve", "message": "go"},
        )
    assert harness.resume_invokes() == []


async def test_resume_intervention_expired_is_unresumable() -> None:
    harness = GraphHarness()
    pending = pending_intervention()
    pending.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    context, interventions = make_context(pending=[pending])
    runner = resume_runner(context, harness)

    with pytest.raises(InterventionResumeError):
        await runner.resume_intervention(
            event=event_for("run-1"),
            interrupt_id="steer-int-1",
            response={"action": "approve", "message": "late"},
        )
    assert [r.status for r in interventions.resolved] == ["expired"]
    assert harness.resume_invokes() == []


async def test_resume_intervention_with_lost_session_fails_the_run() -> None:
    harness = GraphHarness(resumable=False)
    state, context, interventions = await make_through_pause(harness)
    assert state.status is WorkflowStatus.PENDING_INTERVENTION

    runner = resume_runner(context, harness)
    resumed = await runner.resume_intervention(
        event=event_for("run-1"),
        interrupt_id="steer-int-1",
        response={"action": "approve", "message": "go"},
    )

    assert resumed.status is WorkflowStatus.FAILED
    assert harness.resume_invokes() == [], "lost session must not invoke the graph"
    resolved = [r for r in interventions.resolved if r.id == "i-steer-int-1"]
    assert resolved and resolved[0].status == "approved", "the claim stays recorded"


async def test_resume_intervention_rejects_conflicting_second_response() -> None:
    harness = GraphHarness()
    state, context, interventions = await make_through_pause(harness)
    assert state.status is WorkflowStatus.PENDING_INTERVENTION

    runner = resume_runner(context, harness)
    first = await runner.resume_intervention(
        event=event_for("run-1"),
        interrupt_id="steer-int-1",
        response={"action": "approve", "message": "yes"},
    )
    assert first.status in {WorkflowStatus.DELIVERED, WorkflowStatus.FAILED}

    runner = resume_runner(context, harness)
    with pytest.raises(InterventionResumeError):
        await runner.resume_intervention(
            event=event_for("run-1"),
            interrupt_id="steer-int-1",
            response={"action": "deny", "message": "different response"},
        )
    assert len(harness.resume_invokes()) == 1, "conflicting response must not resume again"


async def test_resume_intervention_rejected_graph_fails_the_run() -> None:
    harness = GraphHarness(resume_rejects=True)
    state, context, interventions = await make_through_pause(harness)
    assert state.status is WorkflowStatus.PENDING_INTERVENTION

    runner = resume_runner(context, harness)
    resumed = await runner.resume_intervention(
        event=event_for("run-1"),
        interrupt_id="steer-int-1",
        response={"action": "approve", "message": "go"},
    )

    assert resumed.status is WorkflowStatus.FAILED
    resolved = [r for r in interventions.resolved if r.id == "i-steer-int-1"]
    assert resolved and resolved[0].status == "approved"
