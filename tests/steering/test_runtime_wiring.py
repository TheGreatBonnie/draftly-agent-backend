"""Per-run steering runtime wiring (plan 2026-09-10, Task 6 step 1).

WorkflowContext exposes a ``steering_runtime_factory`` and
``new_steering_runtime`` so the graph factory can build one run-scoped
``SteeringRuntime`` and hand it to ``build_graph_for_run``, which forwards it
into every graph builder.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from draftly.integrations.strands import graph as graph_module
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
