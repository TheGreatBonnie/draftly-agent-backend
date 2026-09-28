"""The context agent's turn budget must be operator-tunable and reach the node.

Mirrors the existing ``writer_max_turns`` plumbing (see
``test_evaluator_budget.py``): ``Settings().strands.context_max_turns`` ->
``WorkflowContext.graph_limits()["context_limits"]`` ->
``build_documentation_graph(context_limits=...)``.

The context agent is the one documentation node with no turn cap at all. In
run ``ce8ea540`` it spent 84.9s across six ``github_read_file``, two
``github_get_tree``, two ``get_diff`` and a PR read before the run died on an
unrelated truncation -- with no ceiling, one unbounded agent decides how much
work a run spends.
"""

from __future__ import annotations

from types import SimpleNamespace

from draftly.app.config import Settings, StrandsConfig
from draftly.workflows.context import WorkflowContext

#: A research agent that reads files, a tree, the diff and the PR needs
#: several turns to gather evidence, but must not be free to keep going.
DEFAULT_CONTEXT_MAX_TURNS = 12


def test_settings_context_budget_defaults(monkeypatch) -> None:
    monkeypatch.delenv("STRANDS_CONTEXT_MAX_TURNS", raising=False)
    assert Settings().strands.context_max_turns == DEFAULT_CONTEXT_MAX_TURNS
    assert StrandsConfig().context_max_turns == DEFAULT_CONTEXT_MAX_TURNS


def test_settings_context_budget_env_override(monkeypatch) -> None:
    monkeypatch.setenv("STRANDS_CONTEXT_MAX_TURNS", "6")
    assert Settings().strands.context_max_turns == 6


def test_graph_limits_composes_context_limits_from_config() -> None:
    class ContextConfig:
        strands = StrandsConfig(context_max_turns=8)

    limits = WorkflowContext(config=ContextConfig()).graph_limits()

    assert limits["context_limits"] == {"turns": 8}


def test_graph_limits_omits_context_limits_without_the_field() -> None:
    """A legacy strands namespace must not emit the kwarg."""

    class LegacyConfig:
        strands = SimpleNamespace(
            max_node_executions=15,
            execution_timeout=3600,
            node_timeout=1200,
            evaluator_max_iterations=2,
        )

    limits = WorkflowContext(config=LegacyConfig()).graph_limits()

    assert "context_limits" not in limits


def test_the_default_context_budget_is_reachable_end_to_end() -> None:
    """A default-configured run must actually bound the context agent."""

    limits = WorkflowContext(config=SimpleNamespace(strands=StrandsConfig())).graph_limits()

    assert limits["context_limits"] == {"turns": DEFAULT_CONTEXT_MAX_TURNS}
