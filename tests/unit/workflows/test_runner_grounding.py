"""WorkflowRunner threads run-grounding (local checkout vs GitHub API) into
the graph factory and the invocation task/state.

A PR run with no checkout but a usable GitHub installation must resolve to
github mode; a run backed by a real local checkout must resolve to local mode
and surface the concrete repo_dir to the evidence agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from strands.multiagent.base import MultiAgentResult, Status

from draftly.workflows.context import WorkflowContext
from draftly.workflows.grounding import (
    DOCS,
    GITHUB,
    LOCAL,
    current_grounding,
)
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowStatus


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
    pull_requests: list[Any] = field(default_factory=list)

    async def save_pull_request(self, pull_request):
        self.pull_requests.append(pull_request)
        return pull_request


@dataclass
class FakeDocumentsRepo:
    upserts: list[dict] = field(default_factory=list)

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        return kwargs


class FakeGraph:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    async def invoke_async(self, task, invocation_state=None, **kwargs):
        del kwargs
        from draftly.memory.scope import current_memory_scope

        scope = current_memory_scope()
        self.calls.append(
            {
                "task": task,
                "invocation_state": invocation_state,
                "memory_scope": scope,
            }
        )
        return self.result


def make_context(**overrides) -> WorkflowContext:
    base: dict[str, Any] = dict(
        repositories=type(
            "Repos",
            (),
            {
                "events": FakeEventsRepo(),
                "reviews": FakeReviewsRepo(),
                "jobs": FakeJobsRepo(),
                "github_workflows": FakeGitHubWorkflowsRepo(),
                "delivery": FakeDeliveryRepo(),
                "documents": FakeDocumentsRepo(),
            },
        )(),
        config=type("Config", (), {"strands": None})(),
    )
    base.update(overrides)
    return WorkflowContext(**base)


def completed_result() -> MultiAgentResult:
    return MultiAgentResult(status=Status.COMPLETED)


def _pr_event() -> dict[str, Any]:
    return {
        "event_id": "evt-grounding",
        "event_type": "pull_request.opened",
        "repository": "acme/api",
        "actor": "dev",
        "source": "github",
        "pull_request": {"number": 7},
    }


async def _run_and_capture(context, event, monkeypatch, checkout_root=None):
    seen: dict[str, Any] = {}
    graph = FakeGraph(completed_result())

    def factory(run_id, surface):
        seen.update(current_grounding())
        return graph

    if checkout_root is not None:
        monkeypatch.setenv("DRAFTLY_REPO_CHECKOUT_ROOT", checkout_root)
    runner = WorkflowRunner(context, graph_factory=factory)
    state = await runner.run(event)
    return state, graph, seen


async def test_github_grounding_for_real_pr_without_local_checkout(
    monkeypatch,
    tmp_path,
) -> None:
    context = make_context()
    event = {**_pr_event(), "installation_id": 71114032}

    state, graph, seen = await _run_and_capture(
        context, event, monkeypatch, checkout_root=str(tmp_path)
    )

    assert state.status == WorkflowStatus.DELIVERED
    assert seen["mode"] == GITHUB
    assert seen["repo_dir"] is None
    task = json.loads(graph.calls[0]["task"])
    assert "repo_dir" not in task
    assert graph.calls[0]["invocation_state"]["installation_id"] == 71114032


async def test_local_grounding_when_checkout_exists_injects_repo_dir(
    monkeypatch,
    tmp_path,
) -> None:
    checkout = tmp_path / "acme" / "api"
    (checkout / ".git").mkdir(parents=True)
    context = make_context()

    state, graph, seen = await _run_and_capture(
        context,
        _pr_event(),
        monkeypatch,
        checkout_root=str(tmp_path),
    )

    assert state.status == WorkflowStatus.DELIVERED
    assert seen["mode"] == LOCAL
    assert seen["repo_dir"] == str(checkout)
    task = json.loads(graph.calls[0]["task"])
    assert task["repo_dir"] == str(checkout)
    assert graph.calls[0]["invocation_state"]["repo_dir"] == str(checkout)


async def test_docs_grounding_without_checkout_or_installation(
    monkeypatch,
    tmp_path,
) -> None:
    context = make_context()

    state, _, seen = await _run_and_capture(
        context,
        _pr_event(),
        monkeypatch,
        checkout_root=str(tmp_path),
    )

    assert state.status == WorkflowStatus.DELIVERED
    assert seen["mode"] == DOCS


async def test_grounding_context_is_reset_after_run(monkeypatch, tmp_path) -> None:
    context = make_context()

    await _run_and_capture(
        context,
        {**_pr_event(), "installation_id": 1},
        monkeypatch,
        checkout_root=str(tmp_path),
    )

    assert current_grounding() == {}


async def test_runner_sets_memory_scope_for_documentation_run(monkeypatch, tmp_path) -> None:
    context = make_context()
    event = {**_pr_event(), "project_id": "org-123"}

    state, graph, _ = await _run_and_capture(
        context, event, monkeypatch, checkout_root=str(tmp_path)
    )

    assert state.status == WorkflowStatus.DELIVERED
    scope = graph.calls[0]["memory_scope"]
    assert scope is not None
    assert scope.org_id == "org-123"
    assert scope.namespace == "documents"


async def test_memory_scope_is_cleared_after_run(monkeypatch, tmp_path) -> None:
    from draftly.memory.scope import current_memory_scope

    context = make_context()

    await _run_and_capture(
        context,
        {**_pr_event(), "project_id": "org-123"},
        monkeypatch,
        checkout_root=str(tmp_path),
    )

    assert current_memory_scope() is None
