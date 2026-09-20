"""Global review node helpers for the documentation graph."""

from __future__ import annotations

import re
from typing import Any

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
