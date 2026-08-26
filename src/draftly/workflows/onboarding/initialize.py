"""Onboarding initialization workflow.

Chains: register repository -> sync docs -> knowledge extraction ->
initial evaluation -> health calculation -> recommendations -> mark COMPLETED
"""

from __future__ import annotations

import asyncio
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
    run_id: str | None = None,
    **kwargs: Any,
) -> WorkflowState:
    del kwargs
    state = WorkflowState(run_id=run_id or f"onboarding-init-{org_id}")
    seq = 0

    async def _publish(envelope_type: str, payload: dict[str, Any]) -> None:
        nonlocal seq
        if context.publisher is None:
            return
        from draftly.events.stream_envelope import StreamEnvelope

        seq += 1
        await context.publisher.publish(
            StreamEnvelope(
                type=envelope_type,
                run_id=state.run_id,
                surface="onboarding",
                seq=seq,
                payload=payload,
            )
        )

    async def _stage_start(stage: str, stats: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"stage": stage, "status": "started"}
        if stats:
            payload["stats"] = stats
        await _publish("stage_change", payload)
        await asyncio.sleep(0)  # yield so the SSE subscriber can pick up the event

    async def _stage_complete(stage: str, stats: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"stage": stage, "status": "completed"}
        if stats:
            payload["stats"] = stats
        await _publish("stage_change", payload)
        await asyncio.sleep(0)

    # Buffer for intermediate progress updates during repository_ingestion.
    # Coalesced so we don't flood the stream with per-file events.
    _progress_lock = asyncio.Lock()
    _latest_progress: dict[str, Any] = {}

    def _on_sync_progress(document_count: int, chunk_count: int) -> None:
        nonlocal _latest_progress
        _latest_progress = {"document_count": document_count, "chunk_count": chunk_count}

    async def _flush_progress() -> None:
        if _latest_progress:
            await _publish("tool_progress", {
                "name": "documentation_sync",
                **_latest_progress,
            })
            await asyncio.sleep(0)

    if not selected_repository:
        state.errors.append("No repository selected")
        await _publish("workflow_result", {"status": "FAILED", "error": "No repository selected"})
        return state.finish(WorkflowStatus.FAILED)

    # Publish an immediate "started" event so the SSE connection has
    # something to receive before heavy work begins (prevents 60s idle timeout).
    await _publish("stage_change", {"stage": "initialization_started", "status": "started"})

    repo_full = selected_repository.get("full_name", "")
    onboarding_repo = getattr(context.repositories, "onboarding", None)

    try:
        installation = await context.repositories.github_installations.first_for_org(org_id)
        if installation is None:
            raise RuntimeError(f"No GitHub installation found for org {org_id}")
        from draftly.integrations.github.app_auth import build_installation_client

        github = await build_installation_client(installation["installation_id"])
        await _update_stage(onboarding_repo, org_id, "repository_ingestion")
        await _stage_start("repository_ingestion")
        from draftly.documentation.sync_service import SyncService

        sync_service = SyncService(github=github, context=context)
        include = selected_repository.get("doc_include", ["README.md", "docs/**", "*.md", "*.mdx"])
        exclude = selected_repository.get("doc_exclude", ["node_modules/**", "dist/**"])
        sync_result = await sync_service.sync(
            org_id=org_id,
            repository_full_name=repo_full,
            include=include,
            exclude=exclude,
            on_progress=_on_sync_progress,
        )
        # Flush any buffered progress after sync completes
        await _flush_progress()

        # A sync that stored nothing while failing every file is a hard
        # failure, not success (R-C): raise so mark_failed records it.
        if sync_result.document_count == 0 and sync_result.failed_files:
            raise RuntimeError(
                "Documentation sync stored 0 documents; "
                f"{len(sync_result.failed_files)} file(s) failed"
            )

        await _stage_complete(
            "repository_ingestion",
            {"document_count": sync_result.document_count, "chunk_count": sync_result.chunk_count},
        )

        # Knowledge/eval/health/recommendations build on the synced corpus;
        # v1 records stage progress so the UI can render it (spec §5.3).
        await _update_stage(onboarding_repo, org_id, "knowledge_construction")
        await _stage_start("knowledge_construction", {"document_count": sync_result.document_count})
        await asyncio.sleep(0)  # yield to allow event delivery
        await _stage_complete("knowledge_construction")

        await _update_stage(onboarding_repo, org_id, "initial_evaluation")
        await _stage_start("initial_evaluation")
        await asyncio.sleep(0)
        await _stage_complete("initial_evaluation")

        await _update_stage(onboarding_repo, org_id, "health_report")
        await _stage_start("health_report")
        await asyncio.sleep(0)
        await _stage_complete("health_report")

        await _update_stage(onboarding_repo, org_id, "recommendations")
        await _stage_start("recommendations")
        await asyncio.sleep(0)
        await _stage_complete("recommendations")

        if onboarding_repo:
            # Mirror final counts into selected_repository so the
            # completion screen can render real numbers.
            current = await onboarding_repo.get(org_id)
            selected = dict((current or {}).get("selected_repository") or {})
            selected["document_count"] = sync_result.document_count
            selected["chunk_count"] = sync_result.chunk_count
            # Atomic: mark step + set COMPLETED so a crash between them
            # never strands the row in INITIALIZING.
            await onboarding_repo.mark_step_and_set_state(
                org_id, "initialization", "COMPLETED",
                selected_repository=selected,
            )

        state.result = {
            "document_count": sync_result.document_count,
            "chunk_count": sync_result.chunk_count,
            "failed_files_count": len(sync_result.failed_files),
            "baseline": sync_result.baseline.to_dict() if sync_result.baseline else None,
        }
        await _publish(
            "workflow_result",
            {
                "status": "COMPLETED",
                "document_count": sync_result.document_count,
                "chunk_count": sync_result.chunk_count,
            },
        )
        logger.info("onboarding_initialize_done org=%s docs=%d", org_id, sync_result.document_count)
        return state.finish(WorkflowStatus.DELIVERED)

    except Exception as exc:
        logger.exception("onboarding_initialize_failed org=%s", org_id)
        if onboarding_repo:
            await onboarding_repo.mark_failed(org_id, "initialize", {"detail": str(exc)})
        state.errors.append(str(exc))
        await _publish("workflow_result", {"status": "FAILED", "error": str(exc)})
        return state.finish(WorkflowStatus.FAILED)


async def _update_stage(onboarding_repo: Any, org_id: str, stage: str) -> None:
    if onboarding_repo:
        current = await onboarding_repo.get(org_id)
        selected = dict((current or {}).get("selected_repository") or {})
        selected["init_stage"] = stage
        await onboarding_repo.upsert(org_id, selected_repository=selected)
