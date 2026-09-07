"""Task 9 E2E: Slack support questions flow through the real ingress chain.

Each ``run_fake_slack_question`` fixture executes the real Slack normalizer,
the real org enrichment boundary, the registered in-process task handler, and
(optionally) the shared review-resume helper, against fake provider clients
and a fake task queue. Provider delivery goes through the real
``slack_post_message`` tool so the reply is produced by the same code path a
production run uses.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from strands.multiagent.base import MultiAgentResult, Status

from draftly.app.composition.rq_jobs import enqueue_support_event
from draftly.app.workers.task_runner import TaskRunner
from draftly.events.support.slack import SlackProcessor
from draftly.persistence.repositories.reviews import ReviewRecord
from draftly.review.resume import resume_review_decision
from draftly.support.identity import SupportIdentityError, enrich_support_event
from draftly.tools.slack.post_message import post_message as slack_post_message
from draftly.workflows.context import WorkflowContext
from draftly.workflows.runner import WorkflowRunner
from draftly.workflows.support.slack_support_workflow import run_slack_support

ORG = {"clerk_org_id": "org-1", "clerk_org_name": "Acme Docs"}
QUESTION = "How do I configure retries?"


class FakeDb:
    def __init__(self, organization=ORG):
        self.organization = organization

    async def fetch_one(self, *args, **kwargs):
        return self.organization


class FakeEventsRepo:
    def __init__(self):
        self.claimed: dict[str, dict[str, Any]] = {}
        self.statuses: dict[str, str] = {}

    async def try_claim(self, event_id, **kwargs):
        if event_id in self.claimed:
            return False
        self.claimed[event_id] = kwargs
        return True

    async def find_by_event_id(self, event_id):
        return {"event_id": event_id, "status": self.statuses.get(event_id, "running")}

    async def get_event_by_id(self, run_id):
        row = dict(self.claimed.get(run_id) or {})
        event = dict(row.get("payload") or {})
        event.setdefault("event_id", run_id)
        return {
            "event_id": run_id,
            "status": self.statuses.get(run_id, "running"),
            "payload": json.dumps(event),
        }

    async def mark_status(self, event_id, status):
        self.statuses[event_id] = status


class FakeJobsRepo:
    def __init__(self):
        self.statuses: list[dict[str, Any]] = []

    async def update_status(self, **kwargs):
        self.statuses.append(kwargs)
        return kwargs


class FakeGitHubWorkflowsRepo:
    def __init__(self):
        self.statuses: list[dict[str, Any]] = []

    async def update_status(self, **kwargs):
        self.statuses.append(kwargs)


class FakeFeedbackOutcomes:
    def __init__(self):
        self.saved: list[tuple] = []

    async def save_outcome(self, *args):
        self.saved.append(args)


class FakeSlackWorkflowRepo:
    def __init__(self):
        self.saved: list[Any] = []
        self.by_source: dict[tuple[str, str], dict[str, Any]] = {}

    async def save_support_delivery(self, receipt):
        self.saved.append(receipt)
        return receipt

    async def get_support_delivery(self, run_id):
        return next((r for r in self.saved if r.run_id == run_id), None)

    async def get_support_delivery_by_source(self, org_id, source_message_id):
        return self.by_source.get((org_id, source_message_id))


class FakeReviews:
    def __init__(self):
        self.records: dict[str, ReviewRecord] = {}
        self.interrupts: list[dict[str, Any]] = []

    async def store_interrupt(self, *, run_id, interrupt_id, reason, workflow_type, org_id):
        self.interrupts.append(
            {
                "run_id": run_id,
                "interrupt_id": interrupt_id,
                "reason": reason,
                "workflow_type": workflow_type,
                "org_id": org_id,
            }
        )

    async def get_review(self, review_id):
        return self.records.get(review_id)

    async def record_decision(self, *, review_id, reviewer_id, decision, comment=None):
        record = self.records[review_id]
        record.status = decision
        record.reviewer_id = reviewer_id
        record.decision = decision
        record.decision_comment = comment
        return record


class FakeSlackClient:
    def __init__(self, *args, **kwargs):
        self.sent: list[dict[str, Any]] = []
        self._counter = 1

    async def send_message(self, channel_id, message, *, thread_id=None, team_id=None):
        self.sent.append({"channel": channel_id, "thread_ts": thread_id})
        ts = str(self._counter + 1)
        self._counter += 1
        return {"ok": True, "ts": ts}


def _wrap_workflow(workflow_func: Any, context: Any) -> Any:
    async def handler(**kwargs: Any) -> Any:
        return await workflow_func(context, **kwargs)

    return handler


def _deliver_node(receipt: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(node_id="deliver", result=SimpleNamespace(structured_output=receipt))


def completed_result(receipt: dict[str, Any]) -> MultiAgentResult:
    result = MultiAgentResult(status=Status.COMPLETED)
    result.execution_order = [_deliver_node(receipt)]
    return result


def interrupted_result() -> MultiAgentResult:
    return MultiAgentResult(
        status=Status.INTERRUPTED,
        interrupts=[SimpleNamespace(id="int-1", reason={"summary": "human review required"})],
    )


def failed_result() -> MultiAgentResult:
    return MultiAgentResult(status=Status.FAILED, execution_count=1)


class _RunnerBuilder:
    def __init__(self, factory: Any) -> None:
        self.factory = factory

    def __call__(self, context: Any, publisher: Any = None) -> WorkflowRunner:
        return WorkflowRunner(context, graph_factory=self.factory, publisher=publisher)


class SupportStubGraph:
    """Stand-in for the support deliver node: real poster tool + fake client.

    Pauses once when ``pause_first`` is set (fresh run) so the review-resume
    helper can drive the second phase; resume delivers on approval, fails on
    rejection.
    """

    def __init__(self, poster: Any, surface: str, *, pause_first: bool = False) -> None:
        self.poster = poster
        self.surface = surface
        self.pause_first = pause_first
        self._paused = False
        self.invocations = 0

    async def invoke_async(self, task: Any, invocation_state: Any = None, **kwargs: Any):
        del kwargs
        self.invocations += 1
        if isinstance(task, list):
            response = task[0]["interruptResponse"]["response"]
            if response.get("approved"):
                return completed_result(await self._deliver())
            return failed_result()
        if self.pause_first and not self._paused:
            self._paused = True
            return interrupted_result()
        return completed_result(await self._deliver())

    async def _deliver(self) -> dict[str, Any]:
        from draftly.integrations.support.runtime import current_support_runtime

        runtime = current_support_runtime()
        assert runtime is not None
        channel = runtime.channel_id
        text = f"Here is how to configure retries. ({runtime.org_id})"
        if runtime.platform == "slack":
            posted = await self.poster(channel=channel, text=text)
            reference = posted["ts"]
        else:
            posted = await self.poster(channel_id=channel, content=text)
            reference = posted["id"]
        return {
            "status": "completed",
            "surface": runtime.platform,
            "delivered_to": channel,
            "reference": reference,
        }


class InProcessWorker:
    """Fake task queue: the durable worker path without RQ fetching."""

    def __init__(self, task_runner: Any) -> None:
        self.task_runner = task_runner
        self.last_result: Any = None
        self.error: BaseException | None = None

    async def run_task(self, name: str, **kwargs: Any) -> Any:
        try:
            self.last_result = await self.task_runner.run(name, **kwargs)
        except Exception as exc:  # pragma: no cover - surfaces fixture failures
            self.error = exc
            raise
        return self.last_result


async def _drain(worker: InProcessWorker) -> Any:
    for _ in range(2000):
        if worker.last_result is not None or worker.error is not None:
            break
        await asyncio.sleep(0)
    if worker.error is not None:
        raise worker.error
    if worker.last_result is None:
        raise RuntimeError("support dispatch did not complete")
    return worker.last_result


def _make_repos() -> SimpleNamespace:
    return SimpleNamespace(
        events=FakeEventsRepo(),
        jobs=FakeJobsRepo(),
        github_workflows=FakeGitHubWorkflowsRepo(),
        reviews=FakeReviews(),
        feedback_outcomes=FakeFeedbackOutcomes(),
        slack_workflows=FakeSlackWorkflowRepo(),
        delivery=SimpleNamespace(
            saved=[],
            save_pull_request=lambda self, pr: self.saved.append(pr),
        ),
    )


@dataclass
class SlackE2E:
    slack: FakeSlackClient
    repos: Any
    db: FakeDb
    enriched: dict[str, Any]


async def _run_question(
    *,
    team_id: str,
    channel: str,
    ts: str,
    question: str,
    review: bool,
    approve: bool,
    monkeypatch: pytest.MonkeyPatch,
    db: FakeDb,
    repos: Any,
) -> tuple[Any, SlackE2E]:
    """Invoke ingress → enrichment → dispatch on shared fake repos."""
    import draftly.integrations.slack.client as slack_client_module
    import draftly.integrations.support.runtime as support_runtime_module
    import draftly.workflows.support.slack_support_workflow as slack_workflow_module

    fake_slack = FakeSlackClient()
    monkeypatch.setattr(slack_client_module, "SlackClient", lambda *a, **kw: fake_slack)
    monkeypatch.setattr(support_runtime_module, "default_slack_installation_store", lambda: None)

    payload = {
        "event": {
            "type": "message",
            "channel": channel,
            "ts": ts,
            "thread_ts": ts,
            "text": question,
            "user": "U1",
        },
        "team_id": team_id,
    }

    processed = await SlackProcessor().process(payload)
    enriched = await enrich_support_event(processed.model_dump(), db=db)

    context = WorkflowContext(repositories=repos, config=SimpleNamespace(strands=None))

    def graph_factory(run_id: str, surface: str) -> SupportStubGraph:
        del run_id
        return SupportStubGraph(slack_post_message, surface, pause_first=review)

    builder = _RunnerBuilder(graph_factory)
    monkeypatch.setattr(slack_workflow_module, "WorkflowRunner", builder)

    runner = WorkflowRunner(context, graph_factory=graph_factory)
    task_runner = TaskRunner()
    task_runner.register("slack_support.enqueue", _wrap_workflow(run_slack_support, context))
    worker = InProcessWorker(task_runner)
    app_state = SimpleNamespace(
        settings=SimpleNamespace(rq_enabled=False),
        rq_queues=None,
        task_handlers=None,
        worker=worker,
    )

    ok = await enqueue_support_event(dict(enriched), app_state=app_state)
    if not ok:
        raise RuntimeError("enqueue_support_event returned False")
    state = await _drain(worker)

    if review:
        assert state.status.value == "pending_review"
        record = ReviewRecord(
            id="review-1",
            org_id=str(enriched["org_id"]),
            thread_id=state.run_id,
            workflow="support",
            tool_name="doc-review",
            tool_args={"interrupt_id": "int-1"},
            action_description="support answer needs review",
            status="pending",
        )
        repos.reviews.records["review-1"] = record
        runtime = SimpleNamespace(
            workflows=SimpleNamespace(runner=runner),
            dependencies=SimpleNamespace(repositories=repos),
        )
        state = await resume_review_decision(
            review_id="review-1",
            approved=approve,
            reviewer_id="reviewer-1",
            comment="Looks good",
            app_state=runtime,
            org_id=str(enriched["org_id"]),
        )

    return state, SlackE2E(slack=fake_slack, repos=repos, db=db, enriched=enriched)


async def run_fake_slack_question(
    team_id: str,
    channel: str,
    ts: str,
    *,
    question: str = QUESTION,
    review: bool = False,
    approve: bool = False,
    monkeypatch: pytest.MonkeyPatch,
    db: FakeDb | None = None,
    repos: Any | None = None,
) -> tuple[Any, SlackE2E]:
    """Execute one Slack question through ingress, enrichment, and dispatch.

    Pass the same ``repos`` across calls to exercise idempotent duplicate
    suppression (a persisted receipt from the first run short-circuits the
    second). ``review=True`` routes through the shared review-resume helper.
    """
    db = db or FakeDb()
    repos = repos or _make_repos()
    return await _run_question(
        team_id=team_id,
        channel=channel,
        ts=ts,
        question=question,
        review=review,
        approve=approve,
        monkeypatch=monkeypatch,
        db=db,
        repos=repos,
    )


async def test_slack_question_is_reviewed_and_replied_once(monkeypatch) -> None:
    run, e2e = await run_fake_slack_question(
        "T1", "C1", "1", review=True, approve=True, monkeypatch=monkeypatch
    )
    assert run.status.value == "delivered"
    assert e2e.slack.sent == [{"channel": "C1", "thread_ts": "1"}]


async def test_slack_direct_answer_replies_in_origin_thread(monkeypatch) -> None:
    run, e2e = await run_fake_slack_question("T1", "C1", "1", monkeypatch=monkeypatch)
    assert run.status.value == "delivered"
    assert e2e.slack.sent == [{"channel": "C1", "thread_ts": "1"}]


async def test_slack_rejected_review_does_not_post(monkeypatch) -> None:
    run, e2e = await run_fake_slack_question(
        "T1", "C1", "1", review=True, approve=False, monkeypatch=monkeypatch
    )
    assert run.status.value == "failed"
    assert e2e.slack.sent == []


async def test_slack_duplicate_event_does_not_post_twice(monkeypatch) -> None:
    repos = _make_repos()
    first, e2e = await run_fake_slack_question(
        "T1", "C1", "1", monkeypatch=monkeypatch, repos=repos
    )
    assert first.status.value == "delivered"
    second, _ = await run_fake_slack_question(
        "T1", "C1", "1", monkeypatch=monkeypatch, repos=repos
    )
    assert second.status.value == "duplicate"
    assert len(e2e.slack.sent) == 1
    assert len(e2e.repos.slack_workflows.saved) == 1


async def test_slack_unlinked_team_is_rejected(monkeypatch) -> None:
    with pytest.raises(SupportIdentityError):
        await run_fake_slack_question(
            "T9", "C1", "1", db=FakeDb(organization=None), monkeypatch=monkeypatch
        )


async def test_slack_question_resolves_org_and_persists_receipt(monkeypatch) -> None:
    run, e2e = await run_fake_slack_question("T1", "C1", "1", monkeypatch=monkeypatch)
    assert e2e.enriched["project_id"] == "org-1"
    assert e2e.enriched["org_id"] == "org-1"
    assert e2e.enriched["source_message_id"] == "C1:1"
    assert run.status.value == "delivered"
    saved = e2e.repos.slack_workflows.saved
    assert len(saved) == 1
    receipt = saved[0]
    assert receipt.run_id == "slack-T1-C1-1"
    assert receipt.org_id == "org-1"
    assert receipt.platform == "slack"
    assert receipt.channel_id == "C1"
    assert receipt.thread_id == "1"
    assert receipt.source_message_id == "C1:1"
    assert receipt.status == "delivered"
