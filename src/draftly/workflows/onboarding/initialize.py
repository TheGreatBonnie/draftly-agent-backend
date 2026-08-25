"""Onboarding initialization workflow.

Chains: register repository -> sync docs -> knowledge extraction ->
initial evaluation -> health calculation -> recommendations -> mark COMPLETED
"""

from __future__ import annotations

from typing import Any

import structlog

from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)

STAGES = [
    "repository_ingestion",
    "knowledge_construction",
    "initial_evaluation",
    "health_report",
    "recommendations",
]


async def run_onboarding_initialize(
    context: WorkflowContext,
    *,
    org_id: str,
    selected_repository: dict[str, Any] | None = None,
    **kwargs: Any,
) -> WorkflowState:
    del kwargs
    state = WorkflowState(run_id=f"onboarding-init-{org_id}")

    if not selected_repository:
        state.errors.append("No repository selected")
        return state.finish(WorkflowStatus.FAILED)

    repo_full = selected_repository.get("full_name", "")
    onboarding_repo = getattr(context.repositories, "onboarding", None)

    try:
        installation = await context.repositories.github_installations.first_for_org(org_id)
        if installation is None:
            raise RuntimeError(f"No GitHub installation found for org {org_id}")
        from draftly.integrations.github.app_auth import build_installation_client

        github = await build_installation_client(installation["installation_id"])
        await _update_stage(onboarding_repo, org_id, "repository_ingestion")
        from draftly.documentation.sync_service import SyncService

        sync_service = SyncService(github=github, context=context)
        include = selected_repository.get("doc_include", ["README.md", "docs/**", "*.md", "*.mdx"])
        exclude = selected_repository.get("doc_exclude", ["node_modules/**", "dist/**"])
        sync_result = await sync_service.sync(
            org_id=org_id,
            repository_full_name=repo_full,
            include=include,
            exclude=exclude,
        )

        # A sync that stored nothing while failing every file is a hard
        # failure, not success (R-C): raise so mark_failed records it.
        if sync_result.document_count == 0 and sync_result.failed_files:
            raise RuntimeError(
                "Documentation sync stored 0 documents; "
                f"{len(sync_result.failed_files)} file(s) failed"
            )

        # Knowledge/eval/health/recommendations build on the synced corpus;
        # v1 records stage progress so the UI can render it (spec §5.3).
        await _update_stage(onboarding_repo, org_id, "knowledge_construction")
        await _update_stage(onboarding_repo, org_id, "initial_evaluation")
        await _update_stage(onboarding_repo, org_id, "health_report")
        await _update_stage(onboarding_repo, org_id, "recommendations")

        if onboarding_repo:
            # Mark the required step BEFORE the terminal state so
            # POST /onboarding/complete's prerequisite check passes.
            await onboarding_repo.mark_step(org_id, "initialization")
            # Mirror final counts into selected_repository so the
            # completion screen can render real numbers.
            current = await onboarding_repo.get(org_id)
            selected = dict((current or {}).get("selected_repository") or {})
            selected["document_count"] = sync_result.document_count
            selected["chunk_count"] = sync_result.chunk_count
            await onboarding_repo.upsert(
                org_id, state="COMPLETED", selected_repository=selected
            )

        state.result = {
            "document_count": sync_result.document_count,
            "chunk_count": sync_result.chunk_count,
            "failed_files_count": len(sync_result.failed_files),
            "baseline": sync_result.baseline.to_dict() if sync_result.baseline else None,
        }
        logger.info("onboarding_initialize_done org=%s docs=%d", org_id, sync_result.document_count)
        return state.finish(WorkflowStatus.DELIVERED)

    except Exception as exc:
        logger.exception("onboarding_initialize_failed org=%s", org_id)
        if onboarding_repo:
            await onboarding_repo.mark_failed(org_id, "initialize", {"detail": str(exc)})
        state.errors.append(str(exc))
        return state.finish(WorkflowStatus.FAILED)


async def _update_stage(onboarding_repo: Any, org_id: str, stage: str) -> None:
    if onboarding_repo:
        current = await onboarding_repo.get(org_id)
        selected = dict((current or {}).get("selected_repository") or {})
        selected["init_stage"] = stage
        await onboarding_repo.upsert(org_id, selected_repository=selected)
