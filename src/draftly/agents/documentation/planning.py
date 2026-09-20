"""Deterministic per-task planning for the documentation fan-out node.

The impact agent emits ``ImpactAnalysis.tasks`` natively; these helpers add
the deterministic fallback used when that structured field is empty (offline
restores, older runs, schema drift) and the shared validation gate.
"""

from __future__ import annotations

from draftly.agents.schemas import DocumentationTask, EvidenceBundle, ImpactAnalysis


def task_id_for(path: str) -> str:
    """Stable, reproduction-friendly task id: the target path."""
    return path


def tasks_from_impact(
    impact: ImpactAnalysis, evidence: EvidenceBundle | None
) -> list[DocumentationTask]:
    """Expand ``affected_documents`` into tasks, scoping evidence by path."""
    items = {item.id: item for item in (evidence.items if evidence else [])}
    action = impact.action if impact.action in ("update", "create") else "update"
    return [
        DocumentationTask(
            id=task_id_for(path),
            path=path,
            action=action,
            reason=impact.rationale or "",
            evidence=[items[path]] if path in items else [],
        )
        for path in impact.affected_documents
    ]


def plan_tasks(
    impact: ImpactAnalysis, evidence: EvidenceBundle | None
) -> list[DocumentationTask]:
    """The task plan of record: LLM-emitted tasks, else the fallback."""
    return list(impact.tasks) if impact.tasks else tasks_from_impact(impact, evidence)
