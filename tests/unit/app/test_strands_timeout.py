"""Strands execution timeout defaults must fit a full PR docs run.

A delivered pull_request documentation run executes ~8 sequential LLM
nodes; the 600s default previously let the execution_timeout kill the run
before the ReviewGate could fire.
"""

from __future__ import annotations

from draftly.app.config import Settings, StrandsConfig
from draftly.orchestration.graphs.documentation_graph import DEFAULT_EXECUTION_TIMEOUT


def test_settings_strands_execution_timeout_has_review_headroom() -> None:
    assert Settings().strands.execution_timeout >= 1800
    assert Settings().strands.node_timeout >= 180


def test_strands_config_default_execution_timeout_has_review_headroom() -> None:
    assert StrandsConfig().execution_timeout >= 1800


def test_documentation_graph_default_execution_timeout_has_headroom() -> None:
    assert DEFAULT_EXECUTION_TIMEOUT >= 1800


