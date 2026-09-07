"""Draft-only content generation workflow.

The first slice is intentionally deterministic at the orchestration boundary:
agents can replace the writers later without changing source provenance,
persistence, evaluation, or review contracts.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from draftly.content.models import (
    ContentChannel,
    ContentPackageStatus,
    ContentRequest,
    ContentVariant,
)
from draftly.content.service import ContentService
from draftly.evaluation.evaluators.content_quality import evaluate_content_variant
from draftly.observability.workflow_logging import log_content_event
from draftly.persistence.repositories.content import ContentRepository
from draftly.workflows.state import WorkflowState, WorkflowStatus
from draftly.workflows.support.models import DocumentationGapRequest


def documentation_gap_to_content_request(
    request: DocumentationGapRequest,
    *,
    source_title: str,
    source_summary: str,
    audience: str,
    tone: str,
    requested_channels: list[ContentChannel],
) -> ContentRequest:
    """Hand a support documentation gap to the content/delivery path.

    Preserves organization, repository, and source event identity so a
    downstream content run stays attributed to the originating support thread.
    """
    evidence = [dict(item) for item in request.evidence] if request.evidence else []
    if not evidence:
        evidence = [{"source_event_ids": list(request.source_event_ids)}]
    return ContentRequest(
        org_id=request.org_id,
        repository_id=request.repository,
        source_event_id=(
            request.source_event_ids[0] if request.source_event_ids else str(uuid4())
        ),
        source_event_type="documentation",
        source_title=source_title,
        source_summary=source_summary,
        source_feedback_ids=list(request.source_event_ids),
        source_gap_id=str((request.change_plan or {}).get("gap_id") or uuid4()),
        source_evidence=evidence,
        requested_channels=list(requested_channels),
        audience=audience,
        tone=tone,
    )


def _evidence(request: ContentRequest) -> list[dict[str, Any]]:
    return [dict(item) for item in request.source_evidence]


def _variant(request: ContentRequest, package_id: str, channel: ContentChannel) -> ContentVariant:
    evidence = _evidence(request)
    if channel is ContentChannel.BLOG:
        body = f"{request.source_summary}\n\nWhat changed\n\n{request.source_title}."
        title = request.source_title
    elif channel is ContentChannel.LINKEDIN:
        body = f"{request.source_title}: {request.source_summary}"
        title = request.source_title
    else:
        body = f"{request.source_title}: {request.source_summary}"
        title = request.source_title
    return ContentVariant(
        id=str(uuid4()),
        package_id=package_id,
        channel=channel,
        title=title,
        body=body,
        evidence=evidence,
        evaluation={
            "groundedness": 1.0,
            "completeness": 0.8,
            "relevance": 0.9,
            "channel_fit": 0.8,
            "blocking_issues": [],
        },
    )


async def run_content_generation(
    request: ContentRequest,
    *,
    repository: ContentRepository,
    run_id: str,
) -> WorkflowState:
    """Generate and persist review-ready variants for a validated request."""
    state = WorkflowState(run_id=run_id, surface="content", event=request.model_dump(mode="json"))
    state.status = WorkflowStatus.RUNNING
    log_content_event(
        "content_workflow_started",
        run_id=run_id,
        org_id=request.org_id,
        source_event_id=request.source_event_id,
        status=state.status.value,
    )
    try:
        service = ContentService(repository)
        package = await service.create(request, run_id=run_id)
        revision = await service.create_revision(
            package_id=package.id,
            revision_number=1,
            reason="initial",
            run_id=run_id,
        )
        variants = []
        for channel in request.requested_channels:
            variant = _variant(request, package.id, channel).model_copy(
                update={"revision_id": revision.id}
            )
            evaluation = evaluate_content_variant(variant)
            variant = variant.model_copy(
                update={
                    "evaluation": {
                        **variant.evaluation,
                        "scores": evaluation["scores"],
                        "blocking_issues": evaluation["issues"],
                    }
                }
            )
            await repository.save_variant(
                package_id=package.id,
                variant=variant.model_dump(mode="json"),
            )
            variants.append(variant.model_dump(mode="json"))
            log_content_event(
                "content_variant_generated",
                run_id=run_id,
                org_id=request.org_id,
                package_id=package.id,
                source_event_id=request.source_event_id,
                channel=channel.value,
                status=variant.status.value,
            )
        await service.update_status(
            org_id=request.org_id,
            package_id=package.id,
            status=ContentPackageStatus.IN_REVIEW,
        )
        state.result = {
            "package_id": package.id,
            "status": "in_review",
            "variants": variants,
            "revision_id": revision.id,
            "source_gap_id": request.source_gap_id,
            "source_feedback_ids": request.source_feedback_ids,
        }
        log_content_event(
            "content_workflow_review_ready",
            run_id=run_id,
            org_id=request.org_id,
            package_id=package.id,
            source_event_id=request.source_event_id,
            status="in_review",
        )
        return state.finish(WorkflowStatus.PENDING_REVIEW)
    except Exception as exc:  # workflow boundary records failures for observability
        state.errors.append(str(exc))
        return state.finish(WorkflowStatus.FAILED)


async def run_content_workflow(
    context: Any,
    *,
    source_event: dict[str, Any],
    **kwargs: Any,
) -> WorkflowState:
    """Workflow-registry adapter for GitHub, manual, and feedback sources."""
    del kwargs
    runner = getattr(context, "runner", None)
    if runner is not None:
        event = dict(source_event)
        event.setdefault("event_id", f"content-{event.get('source_event_id', 'unknown')}")
        event.setdefault("event_type", "content.manual")
        event.setdefault("content_relevant", True)
        return await runner.run(event)
    repository = getattr(getattr(context, "repositories", None), "content", None)
    if repository is None:
        state = WorkflowState(run_id=f"content-{source_event.get('source_event_id', 'unknown')}")
        state.errors.append("content repository unavailable")
        return state.finish(WorkflowStatus.FAILED)
    request = ContentRequest(**source_event)
    return await run_content_generation(
        request,
        repository=repository,
        run_id=str(source_event.get("run_id") or f"content-{request.source_event_id}"),
    )
