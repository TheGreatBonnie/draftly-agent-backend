# tests/unit/app/test_routing_composition.py
"""Composition wiring checks for routing telemetry."""

from __future__ import annotations

import inspect

from draftly.app.dependencies import ModelDependencies, RepositoryDependencies, build_repositories


def test_repository_dependencies_declares_routing_fields():
    params = inspect.signature(RepositoryDependencies.__init__).parameters
    assert "routing" in params
    assert "performance" in params


def test_model_dependencies_declares_stats_store():
    params = inspect.signature(ModelDependencies.__init__).parameters
    assert "stats_store" in params


def test_build_repositories_constructs_routing_repositories():
    src = inspect.getsource(build_repositories)
    assert "DatabaseRoutingStore(" in src
    assert "DatabasePerformanceStore(" in src
