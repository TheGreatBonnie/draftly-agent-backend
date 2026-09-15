"""Evaluator/budget reconciliation tests.

Covers Fix 4 of the github_pr.enqueue death spiral: the node budget must fit
the full docs path (including the escalation tail to the human ReviewGate),
and the evaluator iteration cap must be tunable from config.

NOTE: this lives here (not in test_strands_timeout.py) because that file
imports documentation_graph, which has a pre-existing circular-import cycle
with content_graph; this file deliberately only imports the config/context
modules so it collects cleanly.
"""

from __future__ import annotations

from draftly.app.config import Settings, StrandsConfig
from draftly.workflows.context import WorkflowContext


class StubConfig:
    strands = StrandsConfig(evaluator_max_iterations=3)


def test_node_budget_fits_worst_case_docs_path_with_escalation() -> None:
    """max_node_executions must fit the full docs path even when the
    deterministic evaluator uses all its revision attempts and then escalates
    (classify, context, research, impact, create, evaluate, create,
    evaluate->escalated, changelog, changelog_evaluate, deliver = 11 nodes).

    The old default of 10 killed delivered runs right before `deliver` — the
    observed death-spiral failure. Keep >= 15 (the graph builder default) so
    the ReviewGate/deliver always has budget."""
    assert Settings().strands.max_node_executions >= 15
    assert StrandsConfig().max_node_executions >= 15


def test_evaluator_max_iterations_is_tunable_from_config() -> None:
    """Ops must be able to surface the evaluator iteration cap into the graph
    builder without a code change."""
    ctx = WorkflowContext(config=StubConfig())
    limits = ctx.graph_limits()
    assert limits["evaluator_max_iterations"] == 3


def test_graph_limits_forwards_node_budget() -> None:
    ctx = WorkflowContext(config=StubConfig())
    limits = ctx.graph_limits()
    assert limits["max_node_executions"] == 15
    assert limits["execution_timeout"] == 3600
    assert limits["node_timeout"] == 1200
