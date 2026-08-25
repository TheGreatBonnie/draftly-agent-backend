"""Documentation sync workflow (plan §7.2).

Scheduled full-repository documentation sweep. Orchestrates the sync
service to ingest documentation from GitHub into the document store
and memory index.
"""

from __future__ import annotations

from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)


async def run_documentation_sync(
    context: WorkflowContext,
    *,
    org_id: str | None = None,
    repository_full_name: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    **kwargs: Any,
) -> WorkflowState:
    """Sync documentation from GitHub into the document store."""
    del kwargs
    state = WorkflowState(run_id=f"doc-sync-{id(object())}")

    if org_id is None or repository_full_name is None:
        logger.warning("documentation_sync_missing_params")
        return state.finish(WorkflowStatus.FAILED)

    try:
        installation = await context.repositories.github_installations.first_for_org(org_id)
        if installation is None:
            raise RuntimeError(f"No GitHub installation found for org {org_id}")
        from draftly.integrations.github.app_auth import build_installation_client

        github = await build_installation_client(installation["installation_id"])

        from draftly.documentation.sync_service import SyncService

        service = SyncService(github=github, context=context)
        result = await service.sync(
            org_id=org_id,
            repository_full_name=repository_full_name,
            include=include,
            exclude=exclude,
        )

        result_payload: dict[str, Any] = {
            "document_count": result.document_count,
            "section_count": result.section_count,
            "chunk_count": result.chunk_count,
            "skipped_count": result.skipped_count,
            "failed_files": result.failed_files,
        }

        if result.baseline:
            result_payload["baseline"] = result.baseline.to_dict()

        state.result = result_payload

        logger.info(
            "documentation_sync_done org=%s repo=%s docs=%d chunks=%d",
            org_id,
            repository_full_name,
            result.document_count,
            result.chunk_count,
        )
        return state.finish(WorkflowStatus.DELIVERED)

    except Exception as exc:
        logger.exception("documentation_sync_failed")
        state.errors.append(str(exc))
        return state.finish(WorkflowStatus.FAILED)
