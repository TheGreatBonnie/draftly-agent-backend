"""render_task_prompt + validate_page contract tests.

Includes FanOutWriterNode behavior tests driven by scripted agents (no real model).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from draftly.agents.schemas import DocChangePlan, DocumentationTask, EvidenceItem
from draftly.orchestration.nodes.fan_out import (
    FanOutWriterNode,
    render_task_prompt,
    validate_page,
)


def _task(**overrides) -> DocumentationTask:
    data = dict(
        id="docs/a.md",
        path="docs/a.md",
        action="update",
        reason="behavior changed",
        related_symbols=["Widget"],
        evidence=[EvidenceItem(id="docs/a.md", topic="widgets")],
        requirements=["do not document internals"],
    )
    data.update(overrides)
    return DocumentationTask(**data)


def test_render_task_prompt_carries_path_action_and_scoped_evidence() -> None:
    prompt = render_task_prompt(_task())
    assert "docs/a.md" in prompt
    assert "update" in prompt
    assert "Widget" in prompt
    assert "do not document internals" in prompt
    assert "docs/a.md" in prompt


async def test_validate_page_passes_when_sealed_non_empty() -> None:
    store = SimpleNamespace(
        get_path_latest=_async_get(SimpleNamespace(content="## Guide\n\nbody"))
    )
    ok, reasons = await validate_page(_task(), store, "run-1")
    assert ok is True
    assert reasons == []


async def test_validate_page_fails_when_not_sealed() -> None:
    store = SimpleNamespace(get_path_latest=_async_get(None))
    ok, reasons = await validate_page(_task(), store, "run-1")
    assert ok is False
    assert "no sealed draft" in reasons[0]


async def test_validate_page_fails_on_empty_content() -> None:
    store = SimpleNamespace(get_path_latest=_async_get(SimpleNamespace(content="  ")))
    ok, reasons = await validate_page(_task(), store, "run-1")
    assert ok is False
    assert "empty" in reasons[0]


async def test_validate_page_skips_when_no_store() -> None:
    ok, reasons = await validate_page(_task(), None, "run-1")
    assert ok is True
    assert reasons == []


def _async_get(value):
    async def get_path_latest(*, run_id: str, path: str):
        return value

    return get_path_latest


"""FanOutWriterNode behavior tests (scripted agents, no real model)."""


class _FakeAgent:
    def __init__(self, plan) -> None:
        self.plan = plan
        self.invoked: list[str] = []

    async def invoke_async(self, prompt: str, invocation_state=None, **kwargs):
        self.invoked.append(prompt)
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan


class _FakeFactory:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.agents: list[_FakeAgent] = []

    def create(self, task):
        plan = self.script.pop(0)
        agent = _FakeAgent(plan)
        self.agents.append(agent)
        return agent


def _plan(path: str, action: str = "update") -> DocChangePlan:
    return DocChangePlan(
        repository="acme/api",
        branch="docs/fanout",
        commit_message="docs",
        summary="fan-out",
        files=[{"path": path, "action": action}],
    )


def _input(impact: dict, review: dict | None = None) -> list[dict]:
    lines = ["Original Task: {}", "Inputs from previous nodes:"]
    lines.append("From impact:")
    lines.append(f"  - Agent: {json.dumps(impact)}")
    if review is not None:
        lines.append("From review:")
        lines.append(f"  - Agent: {json.dumps(review)}")
    return [{"text": "\n".join(lines)}]


def _impact(paths: list[str]) -> dict:
    return {
        "action": "update",
        "affected_documents": paths,
        "tasks": [{"id": p, "path": p, "action": "update"} for p in paths],
        "rationale": "behavior changed",
    }


async def test_node_aggregates_two_successful_tasks() -> None:
    factory = _FakeFactory([_plan("docs/a.md"), _plan("docs/b.md", "create")])
    node = FanOutWriterNode(writer_factory=factory, drafts_repo=None)

    result = await node.invoke_async(
        _input(_impact(["docs/a.md", "docs/b.md"])), {"run_id": "run-1"}
    )

    payload = json.loads(result.results["document"].result.message["content"][0]["text"])
    assert payload["task_count"] == 2
    assert {t["path"] for t in payload["tasks"]} == {"docs/a.md", "docs/b.md"}
    assert all(t["ok"] is True for t in payload["tasks"])
    assert {f["path"] for f in payload["files"]} == {"docs/a.md", "docs/b.md"}
    assert payload["failed_tasks"] == []
    assert payload["repository"] == "acme/api"
    assert len(factory.agents) == 2
    assert all(len(agent.invoked) == 1 for agent in factory.agents)


async def test_node_bounds_concurrency() -> None:
    active = 0
    peak = 0

    async def _invoke(prompt: str, invocation_state=None, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _plan("docs/x.md")

    class _SlowAgent:
        async def invoke_async(self, *a, **k):
            return await _invoke(*a, **k)

    class _SlowFactory:
        def create(self, task):
            return _SlowAgent()

    node = FanOutWriterNode(writer_factory=_SlowFactory(), write_concurrency=2, drafts_repo=None)
    await node.invoke_async(
        _input(_impact(["docs/1.md", "docs/2.md", "docs/3.md", "docs/4.md"])), {"run_id": "run-1"}
    )
    assert peak <= 2


async def test_node_retries_only_the_failed_task_once() -> None:
    class _FailOnceAgent:
        def __init__(self, path: str, fail: bool) -> None:
            self.path = path
            self.failed = fail
            self.invoked: list[str] = []

        async def invoke_async(self, prompt: str, invocation_state=None, **kwargs):
            self.invoked.append(prompt)
            if self.failed:
                self.failed = False
                raise RuntimeError("boom")
            return _plan(self.path)

    class _FailOnceFactory:
        def __init__(self) -> None:
            self.created: list[str] = []
            self._failed_once: set[str] = set()

        def create(self, task):
            self.created.append(task.path)
            fail = task.path == "docs/a.md" and task.path not in self._failed_once
            self._failed_once.add(task.path)
            return _FailOnceAgent(task.path, fail=fail)

    factory = _FailOnceFactory()
    node = FanOutWriterNode(writer_factory=factory, drafts_repo=None)
    result = await node.invoke_async(
        _input(_impact(["docs/a.md", "docs/b.md"])), {"run_id": "run-1"}
    )

    payload = json.loads(result.results["document"].result.message["content"][0]["text"])
    assert factory.created.count("docs/a.md") == 2  # failed once, then retried
    assert factory.created.count("docs/b.md") == 1
    assert payload["failed_tasks"] == []
    assert all(t["ok"] is True for t in payload["tasks"])


async def test_node_marks_permanently_failed_task_and_keeps_siblings() -> None:
    class _AlwaysBoom:
        async def invoke_async(self, prompt: str, invocation_state=None, **kwargs):
            raise RuntimeError("always")

    class _AlwaysBoomFactory:
        def create(self, task):
            return _AlwaysBoom()

    node = FanOutWriterNode(writer_factory=_AlwaysBoomFactory(), drafts_repo=None)
    result = await node.invoke_async(
        _input(_impact(["docs/a.md", "docs/b.md"])), {"run_id": "run-1"}
    )

    payload = json.loads(result.results["document"].result.message["content"][0]["text"])
    assert payload["failed_tasks"] == ["docs/a.md", "docs/b.md"]
    assert all(t["ok"] is False for t in payload["tasks"])
    assert [
        t["reasons"][0].startswith("writer failed after retry") for t in payload["tasks"]
    ] == [True, True]


async def test_node_emits_progress_for_each_task() -> None:
    emitted: list[dict] = []

    async def sink(progress: dict) -> None:
        emitted.append(dict(progress))

    factory = _FakeFactory([_plan("docs/a.md"), _plan("docs/b.md")])
    node = FanOutWriterNode(writer_factory=factory, drafts_repo=None, progress_sink=sink)
    await node.invoke_async(_input(_impact(["docs/a.md", "docs/b.md"])), {"run_id": "run-1"})

    assert [e["status"] for e in emitted] == ["running", "running", "completed", "completed"]
    assert all(e["total"] == 2 for e in emitted)
    assert {e["task_id"] for e in emitted} == {"docs/a.md", "docs/b.md"}


async def test_node_corrections_restrict_to_corrected_tasks() -> None:
    factory = _FakeFactory([_plan("docs/b.md")])
    node = FanOutWriterNode(writer_factory=factory, drafts_repo=None)
    review = {
        "verdict": "correct",
        "corrections": [{"task_id": "docs/b.md", "path": "docs/b.md", "instructions": ["tighten"]}],
    }

    await node.invoke_async(
        _input(_impact(["docs/a.md", "docs/b.md"]), review=review), {"run_id": "run-1"}
    )

    assert len(factory.agents) == 1  # only the corrected page was re-dispatched
