"""Deterministic helpers for the documentation fan-out node."""

from __future__ import annotations

from typing import Any

import structlog

from draftly.agents.schemas import DocumentationTask

logger = structlog.get_logger(__name__)


def render_task_prompt(task: DocumentationTask) -> str:
    """One isolated writer prompt for a single page/bundle.

    Scoped to this task's path, action, reason, symbols, requirements, and
    path-matched evidence — other pages' evidence deliberately absent so
    attention never competes across pages.
    """
    lines = [
        f"Documentation task: {task.id}",
        f"Path: {task.path}",
        f"Action: {task.action}",
        f"Reason: {task.reason or 'see evidence'}",
    ]
    if task.related_symbols:
        lines.append("Related symbols: " + ", ".join(task.related_symbols))
    if task.requirements:
        lines.append("Requirements (do not document anything else):")
        lines += [f"- {req}" for req in task.requirements]
    if task.evidence:
        lines.append("Evidence scoped to this page:")
        lines += [
            f"- {item.id}: {item.model_dump(exclude={'id'}, exclude_none=True)}"
            for item in task.evidence
        ]
    return "\n".join(lines)


async def validate_page(
    task: DocumentationTask,
    drafts_repo: Any | None,
    run_id: str | None,
) -> tuple[bool, list[str]]:
    """Deterministic per-page gate: a sealed, non-empty draft exists.

    ``drafts_repo is None`` (offline fixtures/harness) skips the store check;
    per-task validation otherwise rejects unsealed or empty pages.
    """
    if drafts_repo is None:
        return True, []
    latest = await drafts_repo.get_path_latest(run_id=run_id, path=task.path)
    if latest is None:
        return False, ["no sealed draft for this page"]
    if not (latest.content or "").strip():
        return False, ["sealed draft is empty"]
    return True, []
