"""Tests for documentation workflow settings and their validation."""

import pytest

from draftly.app.config import Settings


def test_documentation_workflow_defaults():
    settings = Settings()
    assert settings.documentation_write_concurrency == 3
    assert settings.documentation_evaluation_concurrency == 3
    assert settings.documentation_task_lease_seconds == 300


def test_documentation_workflow_env_overrides(monkeypatch):
    monkeypatch.setenv("DOCUMENTATION_WRITE_CONCURRENCY", "5")
    monkeypatch.setenv("DOCUMENTATION_EVALUATION_CONCURRENCY", "4")
    monkeypatch.setenv("DOCUMENTATION_TASK_LEASE_SECONDS", "120")
    settings = Settings()
    assert settings.documentation_write_concurrency == 5
    assert settings.documentation_evaluation_concurrency == 4
    assert settings.documentation_task_lease_seconds == 120


def test_documentation_workflow_rejects_non_positive_concurrency():
    with pytest.raises(ValueError, match="documentation_write_concurrency"):
        Settings(documentation_write_concurrency=0)
    with pytest.raises(ValueError, match="documentation_evaluation_concurrency"):
        Settings(documentation_evaluation_concurrency=0)
    with pytest.raises(ValueError, match="documentation_task_lease_seconds"):
        Settings(documentation_task_lease_seconds=0)
