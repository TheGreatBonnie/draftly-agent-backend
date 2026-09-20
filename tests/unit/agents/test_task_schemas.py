"""DocumentationTask + ImpactAnalysis.tasks path-validation contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from draftly.agents.schemas import DocumentationTask, ImpactAnalysis


def test_impact_analysis_defaults_tasks_when_absent() -> None:
    impact = ImpactAnalysis(action="update", affected_documents=["docs/a.md"])
    assert impact.tasks == []


def test_impact_analysis_parses_task_list() -> None:
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md", "docs/b.md"],
        tasks=[
            DocumentationTask(id="t1", path="docs/a.md", action="update"),
            DocumentationTask(id="t2", path="docs/b.md", action="create"),
        ],
    )
    assert len(impact.tasks) == 2
    assert impact.tasks[1].action == "create"


def test_task_path_must_be_relative() -> None:
    with pytest.raises(ValidationError, match="relative"):
        ImpactAnalysis(
            action="update",
            affected_documents=["docs/a.md"],
            tasks=[{"id": "t1", "path": "/docs/abs.md", "action": "update"}],
        )


def test_task_path_must_not_escape_repo() -> None:
    with pytest.raises(ValidationError, match="relative"):
        ImpactAnalysis(
            action="update",
            affected_documents=["docs/a.md"],
            tasks=[
                {"id": "t1", "path": "docs/../outside.md", "action": "update"}
            ],
        )


def test_task_paths_must_be_unique_within_a_plan() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ImpactAnalysis(
            action="create",
            affected_documents=["docs/a.md", "docs/b.md"],
            tasks=[
                {"id": "t1", "path": "docs/a.md", "action": "create"},
                {"id": "t2", "path": "docs/a.md", "action": "create"},
            ],
        )
