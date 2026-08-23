"""Unit tests for task registration."""

import pytest
from unittest.mock import MagicMock
from draftly.app.composition.workers import build_task_runner, TASK_REGISTRY


def test_task_runner_registers_sync_repository():
    workflows = MagicMock()
    workflows.registry = MagicMock()
    workflows.registry.get = MagicMock(return_value=MagicMock())
    workflows.context = MagicMock()

    runner = build_task_runner(workflows=workflows, dependencies=MagicMock())

    assert runner.has_task("documentation.sync_repository")
    assert runner.has_task("documentation.sync")
