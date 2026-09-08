"""First-class evaluation persistence on the live PR path (gaps E1/E2/E3)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

from strands.multiagent.base import MultiAgentResult, Status

from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner

PR_EVENT = {
    "event_id": "evt-1",
    "event_type": "pull_request.merged",
    "repository": "acme/api",
    "actor": "dev",
    "source": "github",
    "project_id": "org-1",
}

PASSING_EVAL = {
    "passed": True,
    "score": 0.91,
    "reasons": ["Grounded in 4/4 sources"],
    "iteration": 1,
}
FAILING_EVAL = {
    "passed": False,
    "score": 0.42,
    "reasons": ["Score 0.42 (threshold: 0.70)"],
    "iteration": 3,
}


@dataclass
class FakeEventsRepo:
    async def try_claim(self, event_id, **kwargs):
        return True

    async def find_by_event_id(self, event_id):
        return {"event_id": event_id, "status": "running"}

    async def mark_status(self, event_id, status):
        return None


@dataclass
class FakeJobsRepo:
    calls: list[dict] = field(default_factory=list)

    async def update_status(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


@dataclass
class FakeGitHubWorkflowsRepo:
    async def update_status(self, **kwargs):
        return kwargs


@dataclass
class FakeEvalsRepo:
    created: list[dict] = field(default_factory=list)
    failing: bool = False

    async def create(self, **kwargs):
        if self.failing:
            raise RuntimeError("evaluations db down")
        self.created.append(kwargs)
        return {"id": f"eval-{len(self.created)}"}


@dataclass
class FakeOutcomesRepo:
    saved: list[tuple] = field(default_factory=list)
    failing: bool = False

    async def save_outcome(self, org_id, source_type, source_id, outcome):
        if self.failing:
            raise RuntimeError("outcomes db down")
        self.saved.append((org_id, source_type, source_id, outcome))
        return "out-1"


def evaluate_node(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        node_id="evaluate",
        result=SimpleNamespace(
            result=SimpleNamespace(
                results={
                    "evaluate": SimpleNamespace(
                        result=SimpleNamespace(
                            message={"content": [{"text": json.dumps(payload)}]}
                        )
                    )
                }
            )
        ),
    )


def completed_result(evaluation: dict | None = None) -> MultiAgentResult:
    result = MultiAgentResult(status=Status.COMPLETED)
    if evaluation:
        result.execution_order = [evaluate_node(evaluation)]
    return result


def failed_result(evaluation: dict) -> MultiAgentResult:
    result = MultiAgentResult(status=Status.FAILED)
    result.failed_nodes = 1
    result.execution_order = [
        SimpleNamespace(node_id="update", execution_status=Status.FAILED),
        evaluate_node(evaluation),
    ]
    return result


def interrupted_result(evaluation: dict) -> MultiAgentResult:
    result = MultiAgentResult(status=Status.INTERRUPTED)
    result.interrupts = [SimpleNamespace(id="int-1", reason={"summary": "doc-review"})]
    result.execution_order = [evaluate_node(evaluation)]
    return result


class FakeGraph:
    def __init__(self, result):
        self.result = result

    async def invoke_async(self, task, invocation_state=None, **kwargs):
        return self.result


def make_context(
    evals: FakeEvalsRepo | None = None,
    outcomes: FakeOutcomesRepo | None = None,
) -> WorkflowContext:
    return WorkflowContext(
        repositories=type(
            "Repos", (), {
                "events": FakeEventsRepo(),
                "jobs": FakeJobsRepo(),
                "github_workflows": FakeGitHubWorkflowsRepo(),
                "evaluations": evals or FakeEvalsRepo(),
                "feedback_outcomes": outcomes or FakeOutcomesRepo(),
            }
        )()
    )


async def run_result(result: MultiAgentResult, context: WorkflowContext):
    runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: FakeGraph(result))
    return await runner.run(dict(PR_EVENT))


async def test_completed_persists_evaluation_to_both_stores() -> None:
    context = make_context()
    state = await run_result(completed_result(PASSING_EVAL), context)

    assert state.status.value == "delivered"
    record = context.repositories.evaluations.created[0]
    assert record["evaluation_type"] == "evaluation_gate"
    assert record["run_id"] == "evt-1"
    assert record["org_id"] == "org-1"
    assert record["trace_id"] == "evt-1"
    assert record["passed"] is True
    assert record["status"] == "passed"
    assert record["score"] == 91.0
    org, source_type, source_id, outcome = context.repositories.feedback_outcomes.saved[0]
    assert (org, source_type, source_id) == ("org-1", "evaluation_gate", "evt-1")
    assert outcome["status"] == "passed"


async def test_failed_persists_evaluation_as_failed() -> None:
    context = make_context()
    state = await run_result(failed_result(FAILING_EVAL), context)

    assert state.status.value == "failed"
    record = context.repositories.evaluations.created[0]
    assert record["passed"] is False
    assert record["status"] == "failed"
    assert record["score"] == 42.0
    assert record["metrics"]["reasons"] == ["Score 0.42 (threshold: 0.70)"]
    assert context.repositories.feedback_outcomes.saved[0][3]["status"] == "failed"


async def test_pending_review_carries_evaluation_in_jobs_result() -> None:
    context = make_context()
    state = await run_result(interrupted_result(PASSING_EVAL), context)

    assert state.status.value == "pending_review"
    pending = context.repositories.jobs.calls[-1]
    assert pending["status"] == "pending_review"
    assert pending["result"]["evaluation"]["passed"] is True
    assert context.repositories.evaluations.created == []


async def test_evaluation_persist_failure_is_fail_open() -> None:
    context = make_context(evals=FakeEvalsRepo(failing=True))
    state = await run_result(completed_result(PASSING_EVAL), context)

    assert state.status.value == "delivered"
    org, source_type, source_id, outcome = context.repositories.feedback_outcomes.saved[0]
    assert (org, source_type, source_id) == ("org-1", "evaluation_gate", "evt-1")


async def test_no_evaluation_no_first_class_write() -> None:
    context = make_context()
    state = await run_result(completed_result(), context)

    assert state.status.value == "delivered"
    assert context.repositories.evaluations.created == []
    assert context.repositories.feedback_outcomes.saved == []
