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

STAGE_LABELS: dict[str, str] = {
    "repository_ingestion": "Processing documentation",
    "knowledge_construction": "Building knowledge base",
    "initial_evaluation": "Running evaluation",
    "health_report": "Calculating health",
    "recommendations": "Preparing recommendations",
}


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
    _sync_total_files: int = 0

    def _on_sync_progress(document_count: int, chunk_count: int) -> None:
        nonlocal _latest_progress, _sync_total_files
        _latest_progress = {"document_count": document_count, "chunk_count": chunk_count}
        if document_count > _sync_total_files:
            _sync_total_files = document_count

    async def _emit_stage_progress(stage: str, progress: int) -> None:
        await _publish("stage_progress", {
            "stage": stage,
            "progress": min(max(progress, 0), 100),
        })
        await asyncio.sleep(0)

    async def _flush_progress() -> None:
        if _latest_progress:
            await _publish("tool_progress", {
                "name": "documentation_sync",
                **_latest_progress,
            })
        doc_count = _latest_progress.get("document_count", 0) if _latest_progress else 0
        total = max(_sync_total_files, 1)
        progress = min(int((doc_count / total) * 92), 92) if _sync_total_files > 0 else 0
        await _emit_stage_progress("repository_ingestion", progress)

    if not selected_repository:
        state.errors.append("No repository selected")
        await _publish("workflow_result", {"status": "FAILED", "error": "No repository selected"})
        return state.finish(WorkflowStatus.FAILED)

    # Publish an immediate "started" event so the SSE connection has
    # something to receive before heavy work begins (prevents 60s idle timeout).
    await _publish("stage_change", {"stage": "initialization_started", "status": "started"})

    # Emit stage manifest so the frontend can dynamically render the task list
    await _publish("stage_manifest", {
        "stages": [
            {"id": s, "label": STAGE_LABELS.get(s, s), "order": i}
            for i, s in enumerate(STAGES)
        ]
    })
    await asyncio.sleep(0)

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

        # Stage 2: Knowledge construction — extract from synced chunks
        await _update_stage(onboarding_repo, org_id, "knowledge_construction")
        await _stage_start("knowledge_construction", {"document_count": sync_result.document_count})
        from draftly.workflows.onboarding.stages import (
            run_health_report,
            run_initial_evaluation,
            run_knowledge_construction,
            run_recommendations,
        )

        await _emit_stage_progress("knowledge_construction", 10)
        extraction = await run_knowledge_construction(
            context, org_id=org_id, publish=_publish,
        )
        await _emit_stage_progress("knowledge_construction", 100)
        await _stage_complete("knowledge_construction", {
            "knowledge_count": extraction.knowledge_count,
            "relationship_count": extraction.relationship_count,
            "candidate_count": extraction.candidate_count,
        })

        # Stage 3: Initial evaluation — score corpus quality
        await _update_stage(onboarding_repo, org_id, "initial_evaluation")
        await _stage_start("initial_evaluation")
        await _emit_stage_progress("initial_evaluation", 20)
        eval_result = await run_initial_evaluation(context, org_id=org_id, publish=_publish)
        await _emit_stage_progress("initial_evaluation", 100)
        await _stage_complete("initial_evaluation", {"score": eval_result.score})

        # Stage 4: Health report — aggregate scores
        await _update_stage(onboarding_repo, org_id, "health_report")
        await _stage_start("health_report")
        await _emit_stage_progress("health_report", 30)
        health_result = run_health_report(
            eval_result=eval_result,
            document_count=sync_result.document_count,
            section_count=sync_result.baseline.section_count if sync_result.baseline else 0,
            last_committed_dates=sync_result.last_committed_dates,
        )
        await _emit_stage_progress("health_report", 100)
        await _stage_complete("health_report", {"score": health_result.score})

        # Stage 5: Recommendations — generate suggestions
        await _update_stage(onboarding_repo, org_id, "recommendations")
        await _stage_start("recommendations")
        await _emit_stage_progress("recommendations", 15)
        recs = await run_recommendations(
            context,
            eval_result=eval_result,
            health_result=health_result,
            document_count=sync_result.document_count,
            chunk_count=sync_result.chunk_count,
        )
        await _emit_stage_progress("recommendations", 100)
        await _stage_complete("recommendations", {"count": len(recs)})

        if onboarding_repo:
            current = await onboarding_repo.get(org_id)
            selected = dict((current or {}).get("selected_repository") or {})
            selected["document_count"] = sync_result.document_count
            selected["chunk_count"] = sync_result.chunk_count
            selected["knowledge_count"] = extraction.knowledge_count
            selected["eval_score"] = eval_result.score
            selected["health_score"] = health_result.score
            selected["recommendations"] = [
                {"priority": r.priority, "title": r.title,
                 "detail": r.detail, "category": r.category}
                for r in recs
            ]
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
