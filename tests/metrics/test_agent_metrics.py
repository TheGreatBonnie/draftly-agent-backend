"""Strands-native metrics wiring (spec §Observability surface #5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from draftly.observability.metrics import Metrics


@pytest.fixture
def registry() -> Metrics:
    return Metrics()


def _usage(inp: int, out: int) -> dict[str, int]:
    return {"inputTokens": inp, "outputTokens": out}


def _agent_node(inp: int, out: int) -> Any:
    return SimpleNamespace(metrics=SimpleNamespace(accumulated_usage=_usage(inp, out)))


def _graph_result(nodes: list[tuple[str, Any]]) -> Any:
    return SimpleNamespace(
        execution_order=[SimpleNamespace(node_id=n, result=r) for n, r in nodes]
    )


class TestExtractTokenUsage:
    def test_reads_agent_nodes(self, registry: Metrics, monkeypatch: pytest.MonkeyPatch) -> None:
        import draftly.workflows.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_metrics", registry)
        gr = _graph_result([("writer", _agent_node(100, 20))])
        assert runner_mod.extract_token_usage(gr, model="claude-haiku") == {
            "input": 100,
            "output": 20,
        }
        counters = registry.snapshot()["counters"]
        assert counters["draftly_tokens_input_total"] == 100
        assert counters["draftly_tokens_output_total"] == 20

    def test_swallows_non_agent_nodes(
        self, registry: Metrics, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import draftly.workflows.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_metrics", registry)
        gr = _graph_result(
            [("classify", None), ("swarm", SimpleNamespace(results={}))]
        )
        assert runner_mod.extract_token_usage(gr, model="m") == {"input": 0, "output": 0}
        assert not any(
            k.startswith("draftly_tokens") for k in registry.snapshot()["counters"]
        )

    def test_accumulates_across_nodes(
        self, registry: Metrics, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import draftly.workflows.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_metrics", registry)
        gr = _graph_result(
            [("a", _agent_node(10, 5)), ("b", _agent_node(30, 7))]
        )
        usage = runner_mod.extract_token_usage(gr, model="m")
        assert usage == {"input": 40, "output": 12}
        counters = registry.snapshot()["counters"]
        assert counters["draftly_tokens_input_total"] == 40


class TestStreamingMetrics:
    def _runner(
        self,
        events: list[dict[str, Any]],
        registry: Metrics,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import draftly.workflows.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_metrics", registry)

        class Graph:
            async def stream_async(self, task, invocation_state=None, **kw: Any):
                for raw in events:
                    yield raw

        from tests.workflows.test_phase5_runner_events import PR_EVENT

        context = self._context()
        runner = runner_mod.WorkflowRunner(
            context,
            graph_factory=lambda r, s: Graph(),
            publisher=self._Publisher(),
        )
        return runner, dict(PR_EVENT)

    @staticmethod
    def _context() -> Any:
        from draftly.workflows.context import WorkflowContext

        async def _true(*a: Any, **k: Any) -> bool:
            return True

        async def _none(*a: Any, **k: Any) -> None:
            return None

        events_repo = type(
            "E",
            (),
            {
                "try_claim": staticmethod(_true),
                "find_by_event_id": staticmethod(_none),
                "mark_status": staticmethod(_none),
            },
        )()
        return WorkflowContext(
            repositories=type("R", (), {"events": events_repo})(),
            config=type("C", (), {"strands": None})(),
        )

    class _Publisher:
        def __init__(self) -> None:
            self.published: list[Any] = []

        async def publish(self, envelope: Any) -> None:
            self.published.append(envelope)

    async def test_ttft_observed_on_first_text_delta(
        self, registry: Metrics, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from strands.multiagent.base import Status

        events: list[dict[str, Any]] = [
            {"type": "multiagent_node_start", "node_id": "classify", "node_type": "agent"},
            {
                "type": "multiagent_node_stream",
                "node_id": "writer",
                "event": {"data": "hello"},
            },
            {"result": SimpleNamespace(status=Status.COMPLETED, interrupts=[])},
        ]
        runner, event = self._runner(events, registry, monkeypatch)

        state = await runner.run(event)

        assert state.status.value == "delivered"
        timings = registry.snapshot()["timings"]
        assert any("draftly_run_ttft_ms" in name for name in timings)

    async def test_force_stop_increments_limit_hits(
        self, registry: Metrics, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events = [{"force_stop": True, "force_stop_reason": "max_iterations"}]
        runner, event = self._runner(events, registry, monkeypatch)

        state = await runner.run(event)

        assert state.status.value == "failed"
        assert registry.snapshot()["counters"]["draftly_limit_hits_total"] == 1


class TestAuditFlushCounters:
    async def test_flush_increments_node_and_tool_counters(
        self, registry: Metrics, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import draftly.orchestration.hooks.audit as audit_mod

        monkeypatch.setattr(audit_mod, "_metrics", registry)

        class Repo:
            async def start_run(self, **kw: Any) -> None:
                pass

            async def ensure_run(self, **kw: Any) -> None:
                pass

            async def record_step(self, **kw: Any) -> None:
                pass

            async def finish_run(self, **kw: Any) -> None:
                pass

        steps = [
            {
                "seq": 1,
                "kind": "node",
                "name": "classify",
                "status": "completed",
                "duration_ms": 12,
            },
            {"seq": 2, "kind": "tool", "name": "search_docs", "status": "failed"},
        ]
        await audit_mod._flush_run(Repo(), "evt-x", {"source": "github"}, steps)

        counters = registry.snapshot()["counters"]
        assert counters["draftly_node_steps_total"] == 1
        assert counters["draftly_tool_steps_total"] == 1

    async def test_flush_failure_increments_failure_counter(
        self, registry: Metrics, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import draftly.orchestration.hooks.audit as audit_mod

        monkeypatch.setattr(audit_mod, "_metrics", registry)

        class BrokenRepo:
            async def ensure_run(self, **kw: Any) -> None:
                raise RuntimeError("db down")

        steps = [{"seq": 1, "kind": "node", "name": "bootstrap", "status": "failed"}]
        await audit_mod._flush_run(BrokenRepo(), "evt-x", {"source": "github"}, steps)

        assert registry.snapshot()["counters"]["draftly_audit_flush_failures_total"] == 1
