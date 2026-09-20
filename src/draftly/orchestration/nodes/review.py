"""Global review node helpers for the documentation graph."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import structlog
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status

from draftly.orchestration.nodes.base import agent_result, parse_node_input

logger = structlog.get_logger(__name__)

_FIRST_HEADING = re.compile(r"^#(?!#)\s+(.+)$", re.MULTILINE)
_LINKS = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_REFERENCES_HEADER = re.compile(r"(?m)^##+ *References\b")
_REFERENCE_NUMBER = re.compile(r"(?m)^\s*\d+\.\s")


async def page_summaries(
    drafts_repo: Any | None, run_id: str | None, tasks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deterministic, LLM-free page summaries from sealed store content."""
    if drafts_repo is None:
        return []
    by_path = {f.path: f.content for f in await drafts_repo.get_latest(run_id=run_id) or []}
    summaries: list[dict[str, Any]] = []
    for task in tasks:
        path = str(task.get("path") or "")
        content = by_path.get(path, "")
        if not content:
            continue
        summaries.append(
            {
                "path": path,
                "headings": _FIRST_HEADING.findall(content)[:8],
                "links": _LINKS.findall(_REFERENCES_HEADER.split(content)[0])[:10],
                "first_paragraph": _first_paragraph(content),
                "references": len(_REFERENCE_NUMBER.findall(content)),
                "char_length": len(content),
            }
        )
    return summaries


def _first_paragraph(content: str) -> str:
    body = _FIRST_HEADING.sub("", content, count=1).strip()
    for para in body.split("\n\n"):
        if para.strip():
            return " ".join(para.split())[:400]
    return ""


def _render_review_prompt(
    tasks: list[dict[str, Any]], summaries: list[dict[str, Any]], summary: str
) -> str:
    lines = [
        "Review the documentation change set for cross-page coherence.",
        f"Change-set summary: {summary or '(none)'}",
        "",
        "Generated pages:",
    ]
    for task in tasks:
        lines.append(f"- {task['path']} ({task['action']}, ok={task['ok']})")
    lines.append("")
    lines.append("Compact per-page summaries:")
    for blob in summaries:
        lines.append("- " + json.dumps(blob, sort_keys=True))
    lines.append("")
    lines.append(
        "Return verdict 'clean' when coherent, or 'correct' with targeted "
        "per-page instructions keyed by task_id. Never rewrite pages."
    )
    return "\n".join(lines)


class ReviewNode(MultiAgentBase):
    """Targeted global review of the fan-out change set.

    Deterministic composition of compact summaries -> one reviewer LLM ->
    targeted corrections keyed by task_id. Forwards the document-plan keys so
    evaluate/deliver still receive the fan-out payload via the review edge.
    """

    def __init__(
        self,
        name: str = "review",
        *,
        reviewer_factory: Any | None = None,
        drafts_repo: Any | None = None,
    ) -> None:
        self.name = name
        self._reviewer_factory = reviewer_factory or (lambda: None)
        self.drafts_repo = drafts_repo

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        document = deps.get("document") or {}
        tasks = document.get("tasks") or []
        run_id = (invocation_state or {}).get("run_id")
        summaries = await page_summaries(self.drafts_repo, run_id, tasks)
        prompt = _render_review_prompt(
            tasks, summaries, str(document.get("summary") or "")
        )
        reviewer = self._reviewer_factory()
        verdict = None
        if reviewer is not None:
            result = await reviewer.invoke_async(prompt, invocation_state=invocation_state)
            verdict = getattr(result, "structured_output", None)
        if verdict is None:
            logger.info("review_verdict_degraded", run_id=run_id, reason="no_verdict")
            verdict = SimpleNamespace(verdict="clean", corrections=[])

        known = {t.get("task_id") for t in tasks}
        corrections = [
            {"task_id": c.task_id, "path": c.path, "instructions": list(c.instructions)}
            for c in getattr(verdict, "corrections", []) or []
            if c.task_id in known
        ]

        payload: dict[str, Any] = {
            "verdict": getattr(verdict, "verdict", "clean") or "clean",
            "corrections": corrections,
        }
        for key in (
            "repository", "branch", "commit_message", "summary",
            "files", "tasks", "task_count", "failed_tasks",
        ):
            if key in document:
                payload[key] = document[key]

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: NodeResult(result=agent_result(payload))},
        )
