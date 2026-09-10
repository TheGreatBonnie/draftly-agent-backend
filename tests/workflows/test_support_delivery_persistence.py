"""Task 7: runner persists Slack/Discord delivery receipts and resolves
support threads only after a successful persisted delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from strands.multiagent.base import MultiAgentResult, Status

from draftly.delivery.models import SupportDeliveryReceipt
from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.state import WorkflowState, WorkflowStatus

SLACK_EVENT = {
    "event_id": "slack-1",
    "event_type": "slack.message",
    "source": "slack",
    "project_id": "org-1",
    "team_id": "T1",
    "channel": "C1",
    "thread_ts": "1",
    "source_message_id": "C1:1",
    "question": "How do I configure retries?",
}

DISCORD_EVENT = {
    "event_id": "discord-1",
    "event_type": "discord.message",
    "source": "discord",
    "project_id": "org-1",
    "guild_id": "G1",
    "channel": "C1",
    "thread_ts": "m1",
    "source_message_id": "C1:m1",
    "question": "How do I configure retries?",
}


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
        return {"event_id": event_id, "status": self.statuses.get(event_id, "running")}

    async def mark_status(self, event_id, status):
        self.statuses[event_id] = status


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
class FakeSlackWorkflowRepo:
    saved: list[SupportDeliveryReceipt] = field(default_factory=list)
    by_source: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    async def save_support_delivery(self, receipt):
        self.saved.append(receipt)
        return receipt

    async def get_support_delivery(self, run_id):
        return next((r for r in self.saved if r.run_id == run_id), None)

    async def get_support_delivery_by_source(self, org_id, source_message_id):
        return self.by_source.get((org_id, source_message_id))


@dataclass
class FakeDiscordWorkflowRepo:
    saved: list[SupportDeliveryReceipt] = field(default_factory=list)
    by_source: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    async def save_support_delivery(self, receipt):
        self.saved.append(receipt)
        return receipt

    async def get_support_delivery(self, run_id):
        return next((r for r in self.saved if r.run_id == run_id), None)

    async def get_support_delivery_by_source(self, org_id, source_message_id):
        return self.by_source.get((org_id, source_message_id))


class FakeGraph:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def invoke_async(self, task, invocation_state=None, **kwargs):
        del kwargs
        self.calls.append({"task": task, "invocation_state": invocation_state})
        return self.result


def completed_result(receipt: dict[str, Any]) -> MultiAgentResult:
    result = MultiAgentResult(status=Status.COMPLETED)
    result.execution_order = [
        SimpleNamespace(
            node_id="deliver",
            result=SimpleNamespace(structured_output=receipt),
        )
    ]
    return result


def make_context(
    *,
    slack_repos: bool = True,
    discord_repos: bool = True,
    by_source: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> WorkflowContext:
    slack = FakeSlackWorkflowRepo(by_source=by_source or {})
    discord = FakeDiscordWorkflowRepo(by_source=by_source or {})
    repositories = type("Repos", (), {
        "events": FakeEventsRepo(),
        "jobs": FakeJobsRepo(),
        "github_workflows": FakeGitHubWorkflowsRepo(),
        "delivery": type(
            "D",
            (),
            {
                "saved": [],
                "save_pull_request": lambda self, pr: self.saved.append(pr),
            },
        )(),
    })()
    extras = {}
    if slack_repos:
        extras["slack_workflows"] = slack
    if discord_repos:
        extras["discord_workflows"] = discord
    for name, value in extras.items():
        setattr(repositories, name, value)
    return WorkflowContext(
        repositories=repositories,
        config=type("Config", (), {"strands": None})(),
    )


async def run_downstream(event, *, receipt_status: str = "completed"):
    context = make_context()
    receipt = {
        "status": receipt_status,
        "surface": "slack",
        "delivered_to": "C1",
        "reference": "2",
    }
    graph_result = completed_result(receipt)
    runner = WorkflowRunner(
        context,
        graph_factory=lambda run_id, surface: FakeGraph(graph_result),
    )

    state = await runner.run(dict(event))

    return state, context


async def test_slack_delivery_persists_receipt_provider_message_id() -> None:
    state, context = await run_downstream(SLACK_EVENT)

    assert state.status.value == "delivered"
    assert len(context.repositories.slack_workflows.saved) == 1
    saved = context.repositories.slack_workflows.saved[0]
    assert saved.run_id == "slack-1"
    assert saved.org_id == "org-1"
    assert saved.platform == "slack"
    assert saved.channel_id == "C1"
    assert saved.thread_id == "1"
    assert saved.source_message_id == "C1:1"
    assert saved.provider_message_id == "2"
    assert saved.status == "delivered"


async def test_slack_failed_delivery_persists_failed_status() -> None:
    state, context = await run_downstream(SLACK_EVENT, receipt_status="failed")

    assert state.status.value == "delivered"
    saved = context.repositories.slack_workflows.saved[0]
    assert saved.status == "failed"
    assert saved.provider_message_id == "2"


async def test_discord_delivery_persists_receipt() -> None:
    context = make_context()
    receipt = {
        "status": "completed",
        "surface": "discord",
        "delivered_to": "C1",
        "reference": "m2",
    }
    runner = WorkflowRunner(
        context,
        graph_factory=lambda run_id, surface: FakeGraph(completed_result(receipt)),
    )

    await runner.run(dict(DISCORD_EVENT))

    saved = context.repositories.discord_workflows.saved[0]
    assert saved.platform == "discord"
    assert saved.provider_message_id == "m2"
    assert saved.source_message_id == "C1:m1"
    assert saved.status == "delivered"


async def test_github_receipt_does_not_write_platform_receipt() -> None:
    context = make_context()
    receipt = {
        "status": "completed",
        "surface": "github",
        "delivered_to": "acme/api",
        "reference": "https://github.com/acme/api/pull/42",
    }
    runner = WorkflowRunner(
        context,
        graph_factory=lambda run_id, surface: FakeGraph(completed_result(receipt)),
    )

    await runner.run(
        {
            "event_id": "evt-1",
            "event_type": "pull_request.opened",
            "source": "github",
            "project_id": "org-1",
            "repository": "acme/api",
        }
    )

    assert context.repositories.slack_workflows.saved == []
    assert context.repositories.discord_workflows.saved == []


async def test_existing_slack_delivery_short_circuits_repost() -> None:
    context = make_context(
        by_source={("org-1", "C1:1"): {
            "workflow_id": "slack-1",
            "status": "delivered",
            "provider_message_id": "2",
        }}
    )
    graph = FakeGraph(completed_result({
        "status": "completed",
        "surface": "slack",
        "delivered_to": "C1",
        "reference": "2",
    }))
    runner = WorkflowRunner(context, graph_factory=lambda run_id, surface: graph)

    state = await runner.run(dict(SLACK_EVENT))

    assert state.status.value == "duplicate"
    assert graph.calls == []
    assert context.repositories.slack_workflows.saved == []
    assert state.result["receipt"]["provider_message_id"] == "2"


async def test_existing_only_for_other_source_still_runs() -> None:
    context = make_context(
        by_source={("org-1", "C9:9"): {"status": "delivered"}}
    )
    runner = WorkflowRunner(
        context,
        graph_factory=lambda run_id, surface: FakeGraph(
            completed_result({
                "status": "completed",
                "surface": "slack",
                "delivered_to": "C1",
                "reference": "2",
            })
        ),
    )

    state = await runner.run(dict(SLACK_EVENT))

    assert state.status.value == "delivered"


async def test_github_events_skip_delivery_lookup() -> None:
    context = make_context(by_source={("org-1", "evt-1"): {"status": "delivered"}})
    graph = FakeGraph(completed_result({
        "status": "completed",
        "surface": "github",
        "reference": "https://github.com/acme/api/pull/42",
        "delivered_to": "acme/api",
    }))
    runner = WorkflowRunner(
        context,
        graph_factory=lambda run_id, surface: graph,
    )

    state = await runner.run({
        "event_id": "evt-1",
        "event_type": "pull_request.opened",
        "source": "github",
        "project_id": "org-1",
        "repository": "acme/api",
    })

    assert state.status.value == "delivered"
    assert graph.calls != []


async def test_resolve_support_thread_invoked_after_persisted_delivery() -> None:
    context = make_context()
    receipt = {
        "status": "completed",
        "surface": "discord",
        "delivered_to": "C1",
        "reference": "m2",
    }
    runner = WorkflowRunner(
        context,
        graph_factory=lambda run_id, surface: FakeGraph(completed_result(receipt)),
    )

    await runner.run(dict(DISCORD_EVENT))

    saved = context.repositories.discord_workflows.saved[0]
    assert saved.provider_message_id == "m2"


async def test_resolve_support_thread_reports_receipt_id() -> None:
    from draftly.workflows.support import resolve_support_thread

    context = WorkflowContext()
    state = WorkflowState(run_id="slack-1", event=dict(SLACK_EVENT))
    state.status = WorkflowStatus.DELIVERED
    receipt = SupportDeliveryReceipt(
        run_id="slack-1", org_id="org-1", platform="slack",
        channel_id="C1", thread_id="1", source_message_id="C1:1",
        provider_message_id="2", status="delivered",
    )

    outcome = await resolve_support_thread(context, state, receipt=receipt)

    assert outcome["run_id"] == "slack-1"
    assert outcome["resolved"] is True
    assert outcome["provider_message_id"] == "2"
    assert outcome["receipt_id"] == "2"


async def test_resolve_support_thread_leaves_open_on_failed() -> None:
    from draftly.workflows.support import resolve_support_thread

    state = WorkflowState(run_id="slack-1", event=dict(SLACK_EVENT))
    state.status = WorkflowStatus.FAILED

    outcome = await resolve_support_thread(WorkflowContext(), state)

    assert outcome["resolved"] is False
    assert outcome["status"] == "failed"
