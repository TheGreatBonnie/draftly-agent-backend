"""Deterministic per-task planning for the documentation fan-out node.

The impact agent emits ``ImpactAnalysis.tasks`` natively; these helpers add
the deterministic fallback used when that structured field is empty (offline
restores, older runs, schema drift) and the shared validation gate.

``resolve_task_evidence`` is the single place that populates
``DocumentationTask.evidence``. It reads four sources in precedence order so
that a page the agents researched is never escalated for missing evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any

import structlog

from draftly.agents.schemas import (
    EVIDENCE_EXCERPT_MAX_CHARS,
    DocumentationTask,
    EvidenceBundle,
    EvidenceItem,
    ImpactAnalysis,
)

logger = structlog.get_logger(__name__)

#: The impact agent's prose evidence links a source to a documentation page
#: only through this marker (skills/github-pr-analysis/references/
#: documentation-impact.md:89-93). Anything without it is not page-scoped.
_DOC_MATCH = re.compile(r"Doc match:\s*(?P<path>\S+)", re.IGNORECASE)

#: Tokens shorter than this are too generic to carry page affinity.
_MIN_TOPIC_TOKEN = 3

#: Upper bound on topic-affinity matches, so one page cannot inherit the whole
#: bundle and be held to citing every source.
_MAX_TOPIC_MATCHES = 3


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


def _bundle_items(payload: Any) -> list[EvidenceItem]:
    """Normalize a dependency payload into EvidenceItems, tolerating junk."""
    if isinstance(payload, dict):
        raw = payload.get("items") or payload.get("evidence")
    elif isinstance(payload, list):
        raw = payload
    else:
        return []
    if not isinstance(raw, list):
        return []
    items: list[EvidenceItem] = []
    for entry in raw:
        if isinstance(entry, EvidenceItem):
            items.append(entry)
        elif isinstance(entry, dict):
            items.append(EvidenceItem.model_validate(entry))
    return items


def _bundle_from_deps(deps: Mapping[str, Any]) -> list[EvidenceItem]:
    """Structured evidence, context first then research.

    Mirrors the legacy evaluator's fallback (nodes/evaluate.py:377-381), which
    commit 81f764b dropped. ``research`` is a text swarm and normally yields
    nothing here; ``context`` is the node that emits an EvidenceBundle.
    """
    for source in ("context", "research"):
        items = _bundle_items(deps.get(source))
        if items:
            return items
    return []


def _prose_evidence_by_path(refs: list[str]) -> dict[str, list[EvidenceItem]]:
    """Scope ``ImpactAnalysis.evidence`` prose to pages via ``Doc match:``."""
    by_path: dict[str, list[EvidenceItem]] = {}
    for ref in refs:
        if not isinstance(ref, str):
            continue
        match = _DOC_MATCH.search(ref)
        if not match:
            continue
        path = match.group("path").strip()
        excerpt = ref.strip()
        if len(excerpt) > EVIDENCE_EXCERPT_MAX_CHARS:
            excerpt = excerpt[:EVIDENCE_EXCERPT_MAX_CHARS]
        by_path.setdefault(path, []).append(EvidenceItem(id=path, excerpt=excerpt))
    return by_path


def _fold(token: str) -> str:
    """Drop a plural trailing ``s`` so ``errors`` and ``error`` compare equal.

    Only for tokens long enough that the ``s`` is plausibly a plural, and never
    for ``-ss`` endings, which would mangle ``address`` and ``class``.
    """
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(value: str) -> set[str]:
    return {
        _fold(token)
        for token in re.split(r"[^a-z0-9]+", PurePath(value).stem.lower())
        if len(token) >= _MIN_TOPIC_TOKEN
    }


def _haystack(item: EvidenceItem) -> set[str]:
    """Every token an item can be matched on.

    All four fields are optional and default to ``''``. The context agent
    gathers evidence through ``github_read_file``, so it fills ``url`` and
    ``excerpt`` and leaves ``id`` and ``topic`` blank; tokenising only the
    latter pair scored every real bundle at zero.
    """
    return _tokens(item.id) | _tokens(item.topic) | _tokens(item.url) | _tokens(item.excerpt)


def _topic_matches(items: list[EvidenceItem], path: str) -> list[EvidenceItem]:
    """Best-effort page affinity by token overlap. Guarantees nothing."""
    wanted = _tokens(path)
    if not wanted:
        return []
    scored = [
        (len(wanted & _haystack(item)), index, item) for index, item in enumerate(items)
    ]
    scored = [entry for entry in scored if entry[0] > 0]
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _, _, item in scored[:_MAX_TOPIC_MATCHES]]


def resolve_task_evidence(
    impact: ImpactAnalysis, deps: Mapping[str, Any]
) -> list[DocumentationTask]:
    """The task plan of record, with per-page evidence attached.

    Sources, first non-empty wins per page: the impact agent's own
    ``tasks[].evidence``; its ``ImpactAnalysis.evidence`` prose scoped by
    ``Doc match:``; the structured ``context`` then ``research`` bundle; and
    finally best-effort topic affinity. A page that matches nothing keeps
    ``evidence=[]`` and escalates to human review, as the workflow requires.
    """
    items = _bundle_from_deps(deps)
    prose = _prose_evidence_by_path(impact.evidence)
    by_id = {item.id: item for item in items if item.id}
    tasks = plan_tasks(impact, EvidenceBundle(items=items) if items else None)

    resolved: list[DocumentationTask] = []
    unresolved: list[str] = []
    # Which source rescued each page, so an empty-evidence warning can say why
    # the fallbacks came up short instead of only which pages failed.
    sources: dict[str, str] = {}
    for task in tasks:
        if task.evidence:
            resolved.append(task)
            sources[task.path] = "task"
            continue
        prose_match = prose.get(task.path)
        id_match = [by_id[task.path]] if task.path in by_id else []
        topic_match = _topic_matches(items, task.path)
        if prose_match or id_match or topic_match:
            matched = prose_match or id_match or topic_match
            resolved.append(task.model_copy(update={"evidence": matched}))
            sources[task.path] = (
                "doc_match" if prose_match else "exact_id" if id_match else "topic_affinity"
            )
        else:
            unresolved.append(task.path)
            sources[task.path] = "none"
            resolved.append(task)

    if unresolved:
        logger.warning(
            "page_evidence_empty",
            paths=sorted(unresolved),
            page_count=len(unresolved),
            unresolved_pages=sorted(unresolved),
            resolved_pages=len(tasks) - len(unresolved),
            items=len(items),
            deps=sorted(deps),
            sources=sources,
        )
    return resolved
