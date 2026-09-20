"""plan_tasks/tasks_from_impact fallback contract tests."""

from __future__ import annotations

from draftly.agents.documentation.planning import plan_tasks, task_id_for, tasks_from_impact
from draftly.agents.schemas import DocumentationTask, EvidenceBundle, EvidenceItem, ImpactAnalysis


def _impact(action: str = "update", paths: list[str] | None = None) -> ImpactAnalysis:
    return ImpactAnalysis(
        action=action, affected_documents=paths or ["docs/a.md"], rationale="behavior changed"
    )


def test_tasks_from_impact_expands_paths_with_scoped_evidence() -> None:
    evidence = EvidenceBundle(
        items=[EvidenceItem(id="docs/a.md", topic="widgets")], summary="s"
    )
    tasks = tasks_from_impact(_impact(paths=["docs/a.md", "docs/b.md"]), evidence)
    assert [t.path for t in tasks] == ["docs/a.md", "docs/b.md"]
    assert tasks[0].evidence[0].id == "docs/a.md"
    assert tasks[1].evidence == []
    assert all(t.id == t.path for t in tasks)
    assert all(t.action == "update" for t in tasks)


def test_tasks_from_impact_defaults_action_for_unknown_action() -> None:
    tasks = tasks_from_impact(_impact(action="none"), None)
    assert all(t.action == "update" for t in tasks)


def test_plan_tasks_prefers_llm_tasks_over_fallback() -> None:
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=[DocumentationTask(id="t-hand", path="docs/b.md", action="create")],
    )
    tasks = plan_tasks(impact, None)
    assert [t.id for t in tasks] == ["t-hand"]


def test_plan_tasks_falls_back_when_llm_emitted_none() -> None:
    tasks = plan_tasks(_impact(paths=["docs/a.md"]), None)
    assert [t.id for t in tasks] == ["docs/a.md"]


def test_task_id_for_is_the_path() -> None:
    assert task_id_for("docs/guide.md") == "docs/guide.md"
