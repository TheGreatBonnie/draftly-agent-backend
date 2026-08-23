"""Documentation audit workflow (plan §7.2).

Extended audit: freshness scan, broken internal links, orphaned docs,
duplicate headings. Advisory-only — findings require human review.

Spec §4.6 note: "missing coverage" candidates (search_code queries with
no documentation hits) are explicitly DEFERRED — they require live search
telemetry and are tracked as follow-up work, not silently dropped.
"""

from __future__ import annotations

import posixpath
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import structlog

from draftly.documentation.validator import DocumentationValidator
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

DEFAULT_FRESHNESS_DAYS = 30

logger = structlog.get_logger(__name__)


async def run_documentation_audit(
    context: WorkflowContext,
    *,
    org_id: str | None = None,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
    **kwargs: Any,
) -> WorkflowState:
    """Extended documentation audit: freshness, links, orphans, duplicates."""
    del kwargs
    state = WorkflowState(run_id=f"doc-audit-{datetime.now(UTC):%Y%m%d%H%M%S}")
    documents = getattr(context.repositories, "documents", None) if context else None

    result: dict[str, Any] = {
        "org_id": org_id,
        "stale_documents": [],
        "broken_links": [],
        "orphaned_documents": [],
        "duplicate_headings": [],
        "freshness_days": freshness_days,
    }

    # Org-scoped data access via list_by_org.
    if documents is None or org_id is None:
        logger.warning(
            "documentation_audit_skipped has_documents=%s org_id=%s",
            documents is not None,
            org_id,
        )
        state.result = result
        return state.finish(WorkflowStatus.DELIVERED)

    try:
        all_docs = list(await documents.list_by_org(org_id=org_id, limit=1000))
    except Exception:
        logger.exception("documentation_audit_list_failed")
        state.result = result
        return state.finish(WorkflowStatus.DELIVERED)

    validator = DocumentationValidator()
    known_paths = {doc.get("path", "") for doc in all_docs}
    inbound: set[str] = set()

    for doc in all_docs:
        path = doc.get("path", "")
        content = doc.get("content", "")

        # 1. Freshness — reuses validator.check_freshness (ISO/str/datetime safe)
        days = validator.check_freshness(doc.get("updated_at") or doc.get("created_at"))
        if days is None or days > freshness_days:
            result["stale_documents"].append(doc.get("id", "?"))

        # 2. Links — every target feeds the inbound graph (orphan detection);
        #    check_links additionally flags relative targets missing from the corpus.
        for target in validator.analyzer.links(content):
            inbound.add(_resolve(path, target))
        for target in validator.check_links(content, known_paths=known_paths):
            result["broken_links"].append({"source": path, "target": target})

    # 3. Orphaned documents: nothing links to them
    for path in sorted(known_paths):
        if path and path not in inbound:
            result["orphaned_documents"].append(path)

    # 4. Duplicate headings within a single document
    for doc in all_docs:
        counts = Counter(
            line.strip()
            for line in doc.get("content", "").split("\n")
            if line.strip().startswith("#")
        )
        for heading, count in counts.items():
            if count > 1:
                result["duplicate_headings"].append(
                    {"document": doc.get("path"), "heading": heading, "count": count}
                )

    state.result = result
    logger.info(
        "documentation_audit_done stale=%d broken=%d orphaned=%d dupes=%d",
        len(result["stale_documents"]),
        len(result["broken_links"]),
        len(result["orphaned_documents"]),
        len(result["duplicate_headings"]),
    )
    return state.finish(WorkflowStatus.DELIVERED)


def _resolve(source_path: str, target: str) -> str:
    """Resolve a relative doc link to a repository path."""
    base = posixpath.dirname(source_path)
    return posixpath.normpath(posixpath.join(base, target.split("#", 1)[0]))
