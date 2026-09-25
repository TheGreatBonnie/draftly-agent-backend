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

from types import SimpleNamespace

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


def test_graph_limits_composes_writer_limits_from_config() -> None:
    """Strands writer turn/output budgets reach the graph as one
    ``writer_limits`` kwarg so the page-writer handler can bound the loop
    (instead of relying on the provider's truncation recovery)."""

    class WriterConfig:
        strands = StrandsConfig(writer_max_turns=25, writer_output_tokens=40_000)

    limits = WorkflowContext(config=WriterConfig()).graph_limits()
    assert limits["writer_limits"] == {"turns": 25, "output_tokens": 40_000}


def test_graph_limits_omits_writer_limits_without_writer_budget_fields() -> None:
    """A bare strands namespace without writer budgets must not emit the kwarg
    (backward compat for tests/legacy configs)."""

    class LegacyConfig:
        strands = SimpleNamespace(
            max_node_executions=15,
            execution_timeout=3600,
            node_timeout=1200,
            evaluator_max_iterations=2,
        )

    limits = WorkflowContext(config=LegacyConfig()).graph_limits()
    assert "writer_limits" not in limits


def test_settings_writer_budget_defaults(monkeypatch) -> None:
    monkeypatch.delenv("STRANDS_WRITER_MAX_TURNS", raising=False)
    monkeypatch.delenv("STRANDS_WRITER_OUTPUT_TOKENS", raising=False)
    strands = Settings().strands
    assert strands.writer_max_turns == 60
    assert strands.writer_output_tokens == 48_000


def test_settings_writer_budget_env_override(monkeypatch) -> None:
    monkeypatch.setenv("STRANDS_WRITER_MAX_TURNS", "25")
    monkeypatch.setenv("STRANDS_WRITER_OUTPUT_TOKENS", "40000")
    strands = Settings().strands
    assert strands.writer_max_turns == 25
    assert strands.writer_output_tokens == 40_000
