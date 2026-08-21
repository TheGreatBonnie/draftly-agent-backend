"""Documentation audit workflow (plan §7.2).

Scheduled stale-documentation scan: flags documents not touched within
the freshness window so the feedback loop can prioritize rewrites.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

DEFAULT_FRESHNESS_DAYS = 30

logger = logging.getLogger(__name__)


async def run_documentation_audit(
    context: WorkflowContext,
    *,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
    **kwargs: Any,
) -> WorkflowState:
    """Report documents older than the freshness window."""
    del kwargs
    state = WorkflowState(run_id=f"doc-audit-{datetime.now(UTC):%Y%m%d%H%M%S}")
    documents = getattr(context.repositories, "documents", None) if context else None

    stale: list[str] = []
    if documents is not None:
        lister = getattr(documents, "list_documents", None)
        if lister is not None:
            try:
                cutoff = datetime.now(UTC) - timedelta(days=freshness_days)
                for doc in await lister(limit=1000):
                    updated = getattr(doc, "updated_at", None) or getattr(
                        doc, "created_at", None
                    )
                    if updated is None or _as_aware(updated) < cutoff:
                        stale.append(str(getattr(doc, "id", "?")))
            except Exception:
                logger.exception("documentation_audit_list_failed")

    logger.info("documentation_audit_done stale=%d", len(stale))
    state.result = {"stale_documents": stale, "freshness_days": freshness_days}
    return state.finish(WorkflowStatus.DELIVERED)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
