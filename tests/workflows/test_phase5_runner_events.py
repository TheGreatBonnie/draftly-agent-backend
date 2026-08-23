"""§7.5 verification: WorkflowRunner outcomes, idempotency, dispatcher
routing, and EventComposition normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from strands.interrupt import Interrupt
from strands.multiagent.base import MultiAgentResult, Status
from strands.multiagent.graph import GraphNode, GraphResult

from draftly.events.dispatcher import EventDispatcher
from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState

# ================================================================
# Fakes: in-memory repositories + scripted graphs (no DB, no model)
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
        return {
            "event_id": event_id,
            "status": self.statuses.get(event_id, "running"),
        }

    async def mark_status(self, event_id, status):
        self.statuses[event_id] = status


@dataclass
class FakeReviewsRepo:
    interrupts: list = field(default_factory=list)

    async def store_interrupt(self, **kwargs):
        self.interrupts.append(kwargs)
        return {"id": f"review-{len(self.interrupts)}"}


class FakeGraph:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    async def invoke_async(self, task, invocation_state=None, **kwargs):
        del kwargs
        self.calls.append({"task": task, "invocation_state": invocation_state})
        return self.result


def make_context(**overrides) -> WorkflowContext:
    base: dict[str, Any] = dict(
        repositories=type(
            "Repos", (), {"events": FakeEventsRepo(), "reviews": FakeReviewsRepo()}
        )(),
        config=type("Config", (), {"strands": None})(),
    )
    base.update(overrides)
    return WorkflowContext(**base)


def completed_result() -> MultiAgentResult:
    return MultiAgentResult(status=Status.COMPLETED)


def interrupted_result() -> MultiAgentResult:
    result = MultiAgentResult(status=Status.INTERRUPTED)
    result.interrupts = [
        Interrupt(
            id="v1:before_node_call:deliver:doc-review",
            name="doc-review",
            reason={"summary": "docs update"},
        )
    ]
    return result


@dataclass
class FailedNode:
    node_id: str
    execution_status: Status = Status.FAILED


def failed_result() -> GraphResult:
    return GraphResult(
        status=Status.FAILED,
        failed_nodes=2,
        execution_order=cast(
            list[GraphNode],
            [
                FailedNode("update"),
                FailedNode("evaluate"),
                FailedNode("classify", Status.COMPLETED),
            ],
        ),
    )


PR_EVENT = {
    "event_id": "evt-1",
    "event_type": "pull_request.opened",
    "repository": "acme/api",
    "actor": "dev",
    "source": "github",
    "pull_request": {"number": 1},
}


async def run_with(graph_result, *, pre_claim=False) -> tuple[WorkflowState, WorkflowContext]:
    context = make_context()
    graph = FakeGraph(graph_result)
    runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: graph)
    if pre_claim:
        await context.events.try_claim(PR_EVENT["event_id"], source="github")
    state = await runner.run(dict(PR_EVENT))
    return state, context


# ================================================================
# Runner outcomes (§7.5 #1)
# ================================================================


class TestRunnerOutcomes:
    async def test_completed_marks_delivered(self) -> None:
        state, context = await run_with(completed_result())

        assert state.status.value == "delivered"
        assert state.surface == "pull_request"
        assert context.events.statuses["evt-1"] == "completed"

    async def test_interrupted_stores_and_pends(self) -> None:
        state, context = await run_with(interrupted_result())

        assert state.status.value == "pending_review"
        assert len(state.interrupts) == 1
        assert state.interrupts[0]["interrupt_id"].endswith("doc-review")
        stored = context.reviews.interrupts[0]
        assert stored["run_id"] == "evt-1"
        assert stored["workflow_type"] == "pull_request"
        assert context.events.statuses["evt-1"] == "pending_review"

    async def test_failed_records_failed_nodes(self) -> None:
        state, context = await run_with(failed_result())

        assert state.status.value == "failed"
        assert sorted(state.errors) == ["evaluate", "update"]
        assert context.events.statuses["evt-1"] == "failed"

    async def test_unknown_surface_skips_graph(self) -> None:
        context = make_context()
        graph = FakeGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda r, s: graph)

        state = await runner.run({"event_id": "x", "event_type": "wiki.deleted"})

        assert state.status.value == "skipped"
        assert graph.calls == []

    async def test_invocation_state_carries_review_policy(self) -> None:
        config = type("Config", (), {"strands": type("S", (), {"review_policy": "risky"})()})()
        context = make_context(config=config)
        graph = FakeGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda r, s: graph)

        await runner.run(dict(PR_EVENT))

        invocation_state = graph.calls[0]["invocation_state"]
        assert invocation_state["run_id"] == "evt-1"
        assert invocation_state["review_policy"] == "risky"


# ================================================================
# Idempotency (§7.5 #2)
# ================================================================


class TestIdempotency:
    async def test_replayed_event_is_duplicate_and_graph_not_invoked(self) -> None:
        context = make_context()
        graph = FakeGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda r, s: graph)

        first = await runner.run(dict(PR_EVENT))
        second = await runner.run(dict(PR_EVENT))

        assert first.status.value == "delivered"
        assert second.status.value == "duplicate"
        assert len(graph.calls) == 1  # graph ran exactly once

    async def test_pre_existing_event_is_duplicate(self) -> None:
        state, _ = await run_with(completed_result(), pre_claim=True)

        assert state.status.value == "duplicate"


# ================================================================
# Dispatcher routing (§7.5 #3)
# ================================================================


class TestDispatcherRouting:
    @pytest.mark.parametrize(
        ("event_type", "surface"),
        [
            ("pull_request.opened", "pull_request"),
            ("pull_request.synchronize", "pull_request"),
            ("release.published", "pull_request"),
            ("issues.opened", "issue"),
            ("slack.message", "support"),
            ("discord.message", "support"),
        ],
    )
    def test_routes_to_correct_surface(self, event_type, surface) -> None:
        dispatcher = EventDispatcher()
        assert dispatcher.route({"event_type": event_type}) == surface

    def test_unknown_event_has_no_surface(self) -> None:
        assert EventDispatcher().route({"event_type": "wiki.deleted"}) is None

    async def test_dispatch_invokes_registered_workflow(self) -> None:
        dispatcher = EventDispatcher()
        seen = []

        async def handler(event):
            seen.append(event)
            return "ok"

        dispatcher.register("pull_request.opened", handler)
        assert await dispatcher.dispatch({"event_type": "pull_request.opened"}) == "ok"
        assert seen and seen[0]["event_type"] == "pull_request.opened"

    async def test_dispatch_without_workflow_returns_none(self) -> None:
        assert await EventDispatcher().dispatch({"event_type": "x.y"}) is None


# ================================================================
# EventComposition normalization (§7.5 #4)
# ================================================================


class TestEventComposition:
    def make_composition(self):
        from draftly.app.composition.events import build_event_system

        return build_event_system()

    async def test_github_pr_payload_normalizes(self) -> None:
        composition = self.make_composition()
        raw = {
            "action": "opened",
            "delivery_id": "d-123",
            "repository": {"full_name": "acme/api"},
            "sender": {"login": "dev"},
            "pull_request": {
                "number": 7,
                "title": "Fix widget",
                "state": "open",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
            },
        }

        event = await composition.normalize_github(raw)

        assert event["event_id"] == "d-123"
        assert event["event_type"] == "pull_request.opened"
        assert event["repository"] == "acme/api"
        assert event["actor"] == "dev"
        assert event["pull_request"]["number"] == 7
        assert composition.workflow_type_for(event) == "pull_request"

    async def test_github_issue_payload_normalizes(self) -> None:
        composition = self.make_composition()
        raw = {
            "action": "opened",
            "delivery_id": "d-456",
            "repository": {"full_name": "acme/api"},
            "sender": {"login": "dev"},
            "issue": {"number": 3, "title": "Docs wrong", "labels": [{"name": "docs"}]},
        }

        event = await composition.normalize_github(raw)

        assert event["event_type"] == "issues.opened"
        assert event["issue"]["labels"] == ["docs"]
        assert composition.workflow_type_for(event) == "issue"

    async def test_unhandled_github_payload_raises(self) -> None:
        composition = self.make_composition()

        with pytest.raises(ValueError, match="Unhandled GitHub payload"):
            await composition.normalize_github({"action": "mystery"})

    async def test_slack_payload_normalizes(self) -> None:
        composition = self.make_composition()
        raw = {
            "team_id": "T1",
            "event": {
                "type": "message",
                "text": "How do I configure retries?",
                "user": "U1",
                "channel": "C1",
                "ts": "111.222",
            },
        }

        event = await composition.normalize_slack(raw)

        assert event["event_type"] == "slack.message"
        assert event["source"] == "slack"
        assert event["question"] == "How do I configure retries?"
        assert event["source_message_id"] == "C1:111.222"
        assert composition.workflow_type_for(event) == "support"

    async def test_discord_payload_normalizes(self) -> None:
        composition = self.make_composition()
        raw = {
            "id": "999",
            "guild_id": "G1",
            "channel_id": "C9",
            "content": "Why does auth fail?",
            "author": {"username": "user", "bot": False},
        }

        event = await composition.normalize_discord(raw)

        assert event["event_type"] == "discord.message"
        assert event["source"] == "discord"
        assert event["question"] == "Why does auth fail?"
        assert composition.workflow_type_for(event) == "support"
