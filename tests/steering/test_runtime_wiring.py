"""Per-run steering runtime wiring (plan 2026-09-10, Task 6 step 1).

WorkflowContext exposes a ``steering_runtime_factory`` and
``new_steering_runtime`` so the graph factory can build one run-scoped
``SteeringRuntime`` and hand it to ``build_graph_for_run``, which forwards it
into every graph builder.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from draftly.app.composition.workflows import _steering_runtime_factory
from draftly.integrations.strands import graph as graph_module
from draftly.steering import context as context_module
from draftly.steering.context import SteeringRuntime, SteeringRuntimeConfig
from draftly.workflows.context import WorkflowContext


@pytest.fixture
def run_context() -> WorkflowContext:
    return WorkflowContext(
        steering_runtime_factory=lambda run_id, surface, org_id, project_id, workflow_key: (
            SteeringRuntime.from_context(
                run_id=run_id,
                surface=surface,
                org_id=org_id,
                project_id=project_id,
                workflow_key=workflow_key,
                config=SteeringRuntimeConfig(enabled=True, enforcement_enabled=True),
            )
        ),
    )


def test_new_steering_runtime_forwards_factory_inputs(run_context) -> None:
    runtime = run_context.new_steering_runtime(
        "run-1", "pull_request", "org-1", "project-1", "docs"
    )

    assert isinstance(runtime, SteeringRuntime)
    assert runtime.scope.run_id == "run-1"
    assert runtime.scope.surface == "pull_request"
    assert runtime.scope.org_id == "org-1"
    assert runtime.scope.project_id == "project-1"
    assert runtime.scope.workflow_key == "docs"
    assert runtime.config.enabled is True


def test_steering_runtime_reaches_graph_builder(monkeypatch, run_context) -> None:
    captured: dict[str, object] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setitem(graph_module._BUILDERS, "pull_request", fake_builder)
    runtime = run_context.new_steering_runtime(
        "run-1", "pull_request", "org-1", "project-1", "docs"
    )

    graph_module.build_graph_for_run(
        "run-1",
        surface="pull_request",
        tools_registry=SimpleNamespace(),
        model=object(),
        hooks=[],
        steering_runtime=runtime,
    )

    assert captured["steering_runtime"] is runtime


def test_new_steering_runtime_defaults_without_factory() -> None:
    context = WorkflowContext()

    assert context.new_steering_runtime("run-1", "pull_request") is None


def test_enforcement_requires_durable_steering_repositories() -> None:
    repositories = SimpleNamespace(
        steering_attempts=object(),
        steering_interventions=None,
        agent_runs=None,
    )
    config = SimpleNamespace(
        strands=SimpleNamespace(
            steering_enabled=True,
            steering_enforcement_enabled=True,
            steering_policy_version="v1",
            steering_llm_enabled=False,
            steering_tool_guides_per_call=2,
            steering_model_guides_per_turn=2,
            steering_total_guides_per_agent=5,
            steering_judge_timeout_seconds=10.0,
            steering_reason_max_chars=1_000,
            steering_payload_max_bytes=4 * 1024,
        )
    )

    with pytest.raises(RuntimeError, match="durable steering repositories"):
        _steering_runtime_factory(repositories=repositories, config=config)


async def test_event_sink_failure_is_metriced_and_logged(monkeypatch) -> None:
    metric_calls: list[tuple] = []
    logs: list[tuple] = []
    metrics = SimpleNamespace(
        increment=lambda *args, **kwargs: metric_calls.append((args, kwargs))
    )
    logger = SimpleNamespace(
        warning=lambda *args, **kwargs: logs.append((args, kwargs))
    )
    monkeypatch.setattr(context_module, "_metrics", metrics, raising=False)
    monkeypatch.setattr(context_module, "logger", logger, raising=False)

    async def broken_sink(*_args, **_kwargs):
        raise RuntimeError("publisher down")

    runtime = SteeringRuntime(
        scope=SimpleNamespace(
            run_id="run-1", surface="pull_request", org_id="org-1", project_id="project-1"
        ),
        config=SteeringRuntimeConfig(enabled=True),
        event_sink=broken_sink,
    )

    await runtime.emit(SimpleNamespace(kind="proceed"))

    assert any(args[0] == "draftly_steering_event_sink_failures_total" for args, _ in metric_calls)
    assert logs[0][0][0] == "steering_event_sink_failed"
