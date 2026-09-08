"""§7.5 verification: WorkflowRunner outcomes, idempotency, dispatcher
routing, and EventComposition normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
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
        self.calls.append({"task": task, "invocation_state": invocation_state})
        return self.result


class RejectingGraph(FakeGraph):
    async def invoke_async(self, task, invocation_state=None, **kwargs):
        self.calls.append({"task": task, "invocation_state": invocation_state})
        raise RuntimeError("Rejected by reviewer: needs changes")


def make_context(**overrides) -> WorkflowContext:
    base: dict[str, Any] = dict(
        repositories=type(
            "Repos", (), {
                "events": FakeEventsRepo(),
                "reviews": FakeReviewsRepo(),
                "jobs": FakeJobsRepo(),
                "github_workflows": FakeGitHubWorkflowsRepo(),
                "delivery": FakeDeliveryRepo(),
                "documents": FakeDocumentsRepo(),
            }
        )(),
        config=type("Config", (), {"strands": None})(),
    )
    base.update(overrides)
    return WorkflowContext(**base)


def completed_result() -> MultiAgentResult:
    return MultiAgentResult(status=Status.COMPLETED)


def completed_result_with_evaluation() -> MultiAgentResult:
    result = MultiAgentResult(status=Status.COMPLETED)
    result.execution_order = [
        SimpleNamespace(
            node_id="evaluate",
            result=SimpleNamespace(
                result=SimpleNamespace(
                    results={
                        "evaluate": SimpleNamespace(
                            result=SimpleNamespace(
                                message={
                                    "content": [
                                        {
                                            "text": (
                                                '{"passed": true, "score": 0.91, '
                                                '"reasons": ["grounded"]}'
                                            )
                                        }
                                    ]
                                }
                            )
                        )
                    }
                )
            ),
        )
    ]
    return result


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
    "event_type": "pull_request.merged",
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
        assert [row["status"] for row in context.repositories.jobs.statuses] == [
            "running", "completed"
        ]
        assert [row["status"] for row in context.repositories.github_workflows.statuses] == [
            "running", "completed"
        ]

    async def test_completed_persists_evaluation_details(self) -> None:
        state, context = await run_with(completed_result_with_evaluation())

        assert state.status.value == "delivered"
        completed = context.repositories.jobs.statuses[-1]
        assert completed["result"]["evaluation"] == {
            "passed": True,
            "score": 0.91,
            "reasons": ["grounded"],
        }

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

    async def test_completed_github_delivery_persists_pull_request_receipt(self) -> None:
        context = make_context()
        graph_result = completed_result()
        graph_result.execution_order = [SimpleNamespace(
            node_id="update",
            result=SimpleNamespace(
                structured_output={
                    "repository": "acme/api",
                    "files": [{"path": "docs/auth.md", "content": "# Auth"}],
                }
            ),
        ), SimpleNamespace(
            node_id="deliver",
            result=SimpleNamespace(
                structured_output={
                    "status": "completed",
                    "surface": "github",
                    "delivered_to": "acme/api",
                    "reference": "https://github.com/acme/api/pull/42",
                }
            ),
        )]
        runner = WorkflowRunner(
            context,
            graph_factory=lambda run_id, surface: FakeGraph(graph_result),
        )

        await runner.run({**PR_EVENT, "project_id": "org-1"})

        receipt = context.repositories.delivery.pull_requests[0]
        assert receipt.number == 42
        assert receipt.repository_id == "acme/api"
        assert receipt.org_id == "org-1"
        assert receipt.run_id == "evt-1"
        document = context.repositories.documents.upserts[0]
        assert document["path"] == "docs/auth.md"
        assert document["org_id"] == "org-1"

    async def test_resume_approval_uses_runner_lifecycle_and_org_context(self) -> None:
        context = make_context()
        graph = FakeGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: graph)

        state = await runner.resume_review(
            event={**PR_EVENT, "project_id": "org-1"},
            interrupt_id="int-1",
            response={"approved": True, "comment": "ship it"},
        )

        assert state.status.value == "delivered"
        assert context.events.statuses["evt-1"] == "completed"
        assert context.repositories.jobs.statuses[-1]["status"] == "completed"
        assert context.repositories.github_workflows.statuses[-1]["status"] == "completed"
        assert graph.calls[0]["task"] == [{
            "interruptResponse": {
                "interruptId": "int-1",
                "response": {"approved": True, "comment": "ship it"},
            }
        }]
        assert graph.calls[0]["invocation_state"]["project_id"] == "org-1"

    async def test_resume_rejection_finalizes_failed_workflow(self) -> None:
        context = make_context()
        graph = RejectingGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: graph)

        state = await runner.resume_review(
            event={**PR_EVENT, "project_id": "org-1"},
            interrupt_id="int-1",
            response={"approved": False, "comment": "needs changes"},
        )

        assert state.status.value == "failed"
        assert context.events.statuses["evt-1"] == "failed"
        assert context.repositories.jobs.statuses[-1]["status"] == "failed"

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


class TestRunnerMergedOnlyGate:
    async def _run_event(self, event_type: str) -> tuple[WorkflowState, object]:
        from draftly.workflows.runner import WorkflowRunner

        context = make_context()
        graph = FakeGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda r, s: graph)
        state = await runner.run(
            {"event_id": "evt-gate", "event_type": event_type, "source": "github"}
        )
        return state, context

    async def test_opened_pr_runs_graph(self) -> None:
        state, context = await self._run_event("pull_request.opened")
        assert state.status.value == "delivered"
        assert context.events.statuses["evt-gate"] == "completed"

    async def test_edited_pr_skips(self) -> None:
        state, _ = await self._run_event("pull_request.edited")
        assert state.status.value == "skipped"

    async def test_closed_not_merged_pr_skips(self) -> None:
        state, _ = await self._run_event("pull_request.closed")
        assert state.status.value == "skipped"

    async def test_merged_pr_runs_graph(self) -> None:
        state, context = await self._run_event("pull_request.merged")
        assert state.status.value == "delivered"
        assert context.events.statuses["evt-gate"] == "completed"

    async def test_push_and_release_not_skipped(self) -> None:
        # Guard: push/release share the pull_request surface but must keep running.
        for event_type in ("push.pushed", "release.published"):
            ctx = make_context()
            graph = FakeGraph(completed_result())
            runner = WorkflowRunner(
                ctx, graph_factory=lambda r, s: graph, dispatcher=EventDispatcher()
            )
            state = await runner.run(
                {"event_id": event_type, "event_type": event_type, "source": "github"}
            )
            assert state.status.value == "delivered", event_type


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


# ================================================================
# Streaming mode — publisher wired => stream_async path
# ================================================================

from draftly.events.stream_envelope import StreamEnvelope  # noqa: E402


class StreamingFakeGraph(FakeGraph):
    """Emits scripted events then the terminal result event."""

    def __init__(self, result, events=None):
        super().__init__(result)
        self.events = list(events or [])

    async def stream_async(self, task, invocation_state=None, **kwargs):
        del kwargs
        self.calls.append({"task": task, "invocation_state": invocation_state})
        for raw in self.events:
            yield raw
        yield {"result": self.result}


class RecordingPublisher:
    def __init__(self):
        self.published: list[StreamEnvelope] = []

    async def publish(self, envelope):
        self.published.append(envelope)


STREAM_EVENTS = [
    {"type": "multiagent_node_start", "node_id": "classify", "node_type": "agent"},
    {"init_event_loop": True},  # noise — must be dropped
    {
        "type": "multiagent_node_stream",
        "node_id": "writer",
        "event": {"data": "hello "},
    },
    {
        "type": "multiagent_node_stop",
        "node_id": "classify",
        "node_result": {"status": "COMPLETED", "duration": 0.5},
    },
    {
        "type": "multiagent_handoff",
        "from_node_ids": ["classify"],
        "to_node_ids": ["write"],
    },
]


class TestRunnerStreaming:
    async def test_publisher_wired_streams_and_preserves_outcome(self) -> None:
        publisher = RecordingPublisher()
        context = make_context()
        graph = StreamingFakeGraph(completed_result(), STREAM_EVENTS)
        runner = WorkflowRunner(
            context, graph_factory=lambda r, s: graph, publisher=publisher
        )

        state = await runner.run(dict(PR_EVENT))

        assert state.status.value == "delivered"
        types = [e.type for e in publisher.published]
        assert types == [
            "node_start",
            "text_delta",
            "node_stop",
            "handoff",
            "workflow_result",
        ]
        seqs = [e.seq for e in publisher.published]
        assert seqs == [1, 2, 3, 4, 5]
        assert all(e.run_id == "evt-1" for e in publisher.published)
        assert all(e.surface == "pull_request" for e in publisher.published)
        assert context.events.statuses["evt-1"] == "completed"

    async def test_no_publisher_keeps_invoke_async_path(self) -> None:
        context = make_context()
        graph = FakeGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda r, s: graph)

        state = await runner.run(dict(PR_EVENT))

        assert state.status.value == "delivered"
        assert len(graph.calls) == 1

    async def test_streaming_interrupt_outcome_matches_invoke_path(self) -> None:
        publisher = RecordingPublisher()
        context = make_context()
        graph = StreamingFakeGraph(interrupted_result(), STREAM_EVENTS[:1])
        runner = WorkflowRunner(
            context, graph_factory=lambda r, s: graph, publisher=publisher
        )

        state = await runner.run(dict(PR_EVENT))

        assert state.status.value == "pending_review"
        assert len(state.interrupts) == 1
        terminal = publisher.published[-1]
        assert terminal.type == "workflow_result"
        assert terminal.payload["status"] == "INTERRUPTED"
        assert terminal.payload["interrupts"][0]["id"].endswith("doc-review")

    async def test_stream_missing_result_raises(self) -> None:
        class NoResultGraph(StreamingFakeGraph):
            async def stream_async(self, task, invocation_state=None, **kwargs):
                del kwargs, task, invocation_state
                for raw in self.events:
                    yield raw

        publisher = RecordingPublisher()
        context = make_context()
        graph = NoResultGraph(completed_result(), STREAM_EVENTS[:1])
        runner = WorkflowRunner(
            context, graph_factory=lambda r, s: graph, publisher=publisher
        )

        with pytest.raises(RuntimeError, match="without a result"):
            await runner.run(dict(PR_EVENT))

    async def test_force_stop_maps_to_failed_outcome(self) -> None:
        class ForceStopGraph(FakeGraph):
            async def stream_async(self, task, invocation_state=None, **kwargs):
                del kwargs, task, invocation_state
                yield {"force_stop": True, "force_stop_reason": "max_iterations"}

        publisher = RecordingPublisher()
        context = make_context()
        graph = ForceStopGraph(completed_result())
        runner = WorkflowRunner(
            context, graph_factory=lambda r, s: graph, publisher=publisher
        )

        state = await runner.run(dict(PR_EVENT))

        assert state.status.value == "failed"
        terminal = publisher.published[-1]
        assert terminal.type == "workflow_result"
        assert terminal.payload["status"] == "FAILED"

    async def test_streaming_node_timeout_maps_to_failed_outcome(self) -> None:
        class TimeoutGraph(FakeGraph):
            async def stream_async(self, task, invocation_state=None, **kwargs):
                del kwargs
                self.calls.append({"task": task, "invocation_state": invocation_state})
                for raw in STREAM_EVENTS:
                    yield raw
                raise Exception("Node 'research' execution timed out after 180s")

        publisher = RecordingPublisher()
        context = make_context()
        graph = TimeoutGraph(completed_result())
        runner = WorkflowRunner(
            context, graph_factory=lambda r, s: graph, publisher=publisher
        )

        state = await runner.run(dict(PR_EVENT))

        assert state.status.value == "failed"
        assert state.errors == ["research"]
        assert context.events.statuses["evt-1"] == "failed"
        assert [row["status"] for row in context.repositories.jobs.statuses][-1] == "failed"
        assert context.repositories.github_workflows.statuses[-1]["status"] == "failed"
        terminal = publisher.published[-1]
        assert terminal.type == "workflow_result"
        assert terminal.payload["status"] == "FAILED"
        assert terminal.node_id == "research"
        # Partial events streamed before the timeout are preserved.
        assert [e.type for e in publisher.published][:4] == [
            "node_start",
            "text_delta",
            "node_stop",
            "handoff",
        ]

    async def test_invoke_node_timeout_maps_to_failed_outcome(self) -> None:
        class TimeoutInvokeGraph(FakeGraph):
            async def invoke_async(self, task, invocation_state=None, **kwargs):
                del kwargs
                self.calls.append({"task": task, "invocation_state": invocation_state})
                raise Exception("Node 'update' execution timed out after 600s")

        context = make_context()
        graph = TimeoutInvokeGraph(completed_result())
        runner = WorkflowRunner(context, graph_factory=lambda r, s: graph)

        state = await runner.run(dict(PR_EVENT))

        assert state.status.value == "failed"
        assert state.errors == ["update"]
        assert context.events.statuses["evt-1"] == "failed"
        assert context.repositories.jobs.statuses[-1]["status"] == "failed"

    async def test_non_timeout_exception_still_propagates(self) -> None:
        class BoomGraph(FakeGraph):
            async def stream_async(self, task, invocation_state=None, **kwargs):
                del kwargs, task, invocation_state
                yield {"force_stop": True, "force_stop_reason": "max_iterations"}
                raise RuntimeError("orcarouter refused")

        publisher = RecordingPublisher()
        context = make_context()
        graph = BoomGraph(completed_result())
        runner = WorkflowRunner(
            context, graph_factory=lambda r, s: graph, publisher=publisher
        )

        with pytest.raises(RuntimeError, match="orcarouter refused"):
            await runner.run(dict(PR_EVENT))
