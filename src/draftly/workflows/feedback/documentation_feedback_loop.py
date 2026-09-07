"""Documentation feedback loop workflow (plan §7.2).

Scheduled: support questions → feedback graph → documentation gaps.
Questions come from the job payload or, when absent, from recent support
messages (channel name doubles as the clustering topic).
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from strands.multiagent.base import Status

from draftly.content.models import ContentChannel, ContentRequest
from draftly.feedback.models import ContentOpportunity, DocumentationGapCandidate
from draftly.workflows.content.content_generation import run_content_generation
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus

logger = structlog.get_logger(__name__)


async def run_feedback_loop(
    context: WorkflowContext,
    *,
    questions: list[dict[str, Any]] | None = None,
    org_id: str | None = None,
    gap_threshold: int = 2,
    **kwargs: Any,
) -> WorkflowState:
    """Run the deterministic feedback graph over support questions."""
    del kwargs
    state = WorkflowState(run_id=f"feedback-{id(object())}")

    if questions is None:
        if not org_id:
            feedback_repository = getattr(
                getattr(getattr(context, "repositories", None), "feedback", None),
                "list_org_ids",
                None,
            )
            if feedback_repository is None:
                state.errors.append(
                    "org_id is required when organization enumeration is unavailable"
                )
                return state.finish(WorkflowStatus.FAILED)
            organization_ids = await feedback_repository()
            results = []
            for organization_id in organization_ids:
                child = await run_feedback_loop(
                    context,
                    org_id=organization_id,
                    gap_threshold=gap_threshold,
                )
                results.append(child.to_dict())
            state.result = {"organizations": results}
            return state.finish(WorkflowStatus.DELIVERED)
        questions = await _gather_questions(context, org_id=org_id)

    from draftly.orchestration.graphs.feedback_graph import build_feedback_graph

    graph = build_feedback_graph(gap_threshold=gap_threshold)
    result = await graph.invoke_async(
        json.dumps({"questions": questions}),
        invocation_state={"run_id": state.run_id},
    )
    state.result = result

    if result.status == Status.COMPLETED:
        if org_id:
            try:
                await _persist_gaps(context, org_id=org_id, result=result)
            except Exception as exc:
                logger.exception("feedback_gap_persist_failed", run_id=state.run_id)
                state.errors.append(str(exc))
                return state.finish(WorkflowStatus.FAILED)
        return state.finish(WorkflowStatus.DELIVERED)
    state.errors.append(f"feedback graph ended {result.status}")
    return state.finish(WorkflowStatus.FAILED)


async def _persist_gaps(context: WorkflowContext, *, org_id: str, result: Any) -> list[str]:
    """Persist graph candidates when the composed context has a gap repository."""
    repository = getattr(getattr(context, "repositories", None), "documentation_gaps", None)
    if repository is None:
        return []

    try:
        enqueued_result = result.results["enqueue"].result
        agent_result = enqueued_result.results["enqueue"].result
        text = agent_result.message["content"][0]["text"]
        enqueued = json.loads(text).get("enqueued", [])
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        enqueued = []

    gap_ids: list[str] = []
    for gap in enqueued:
        candidate = DocumentationGapCandidate(
            topic=str(gap.get("topic") or "general"),
            occurrences=int(gap.get("count") or 0),
            platforms=list(gap.get("platforms") or []),
            sample_questions=list(gap.get("sample_questions") or []),
            metadata={
                "source_event_ids": list(gap.get("source_event_ids") or []),
                "source_urls": list(gap.get("source_urls") or []),
            },
        )
        gap_id = await repository.upsert_candidate(org_id, candidate)
        await repository.set_outcome(gap_id, "documentation")
        gap_ids.append(gap_id)
    if isinstance(getattr(result, "results", None), dict):
        result.results.setdefault("persisted_gaps", gap_ids)
    return gap_ids


async def dispatch_content_opportunity(
    context: WorkflowContext,
    opportunity: ContentOpportunity,
    *,
    repository_id: str,
    run_id: str | None = None,
) -> str:
    """Bridge one durable feedback gap into a reviewable content package."""
    repository = getattr(getattr(context, "repositories", None), "content", None)
    if repository is None:
        raise RuntimeError("content repository unavailable")
    request = ContentRequest(
        org_id=opportunity.org_id,
        repository_id=repository_id,
        source_event_id=opportunity.gap_id,
        source_event_type="feedback_gap",
        source_title=opportunity.topic,
        source_summary=opportunity.reason,
        source_feedback_ids=opportunity.source_feedback_ids,
        source_gap_id=opportunity.gap_id,
        source_evidence=opportunity.evidence or [{"source_id": opportunity.gap_id}],
        requested_channels=[
            ContentChannel(channel) for channel in opportunity.recommended_channels
        ],
        audience="users with the recurring question",
        tone="clear and helpful",
    )
    event = request.model_dump(mode="json")
    event.update({
        "event_id": run_id or f"content-gap-{opportunity.gap_id}",
        "event_type": "content.feedback_gap",
        "project_id": opportunity.org_id,
        "content_relevant": True,
    })
    runner = getattr(context, "runner", None)
    if runner is not None:
        state = await runner.run(event)
        package = await repository.get_by_source(
            org_id=opportunity.org_id,
            repository_id=request.repository_id,
            source_event_type=request.source_event_type,
            source_event_id=request.source_event_id,
        )
        if package is None:
            raise RuntimeError("content opportunity did not persist a package")
        return package.id
    else:
        state = await run_content_generation(
            request,
            repository=repository,
            run_id=event["event_id"],
        )
    if state.result is None:
        raise RuntimeError("content opportunity dispatch failed: " + "; ".join(state.errors))
    return str(state.result["package_id"])


async def _gather_questions(
    context: WorkflowContext,
    *,
    org_id: str,
) -> list[dict[str, Any]]:
    """Collect questions via FeedbackService when wired, else raw rows."""
    feedback = getattr(context, "feedback", None) if context else None
    if feedback is not None and hasattr(feedback, "collect_questions"):
        try:
            items = await feedback.collect_questions(org_id=org_id)
            if items:
                deduplicator = getattr(feedback, "deduplicator", None)
                if deduplicator is not None:
                    items = deduplicator.deduplicate(items)
                return [
                    {
                        "topic": item.topic,
                        "question": item.content,
                        "source": item.platform,
                        "source_event_id": item.source_event_id or item.source_message_id,
                        "source_url": item.source_url,
                        "org_id": item.org_id or org_id,
                    }
                    for item in items
                ]
        except Exception:
            logger.exception("feedback_service_collect_failed")
    return await _collect_questions(context, org_id=org_id)


async def _collect_questions(
    context: WorkflowContext,
    *,
    org_id: str,
) -> list[dict[str, Any]]:
    """Fallback: fetch recent support messages as raw question dicts."""
    support = getattr(context.repositories, "support", None) if context else None
    if support is None:
        return []
    try:
        messages = await support.search_messages("%", org_id=org_id, limit=100)
    except Exception:
        logger.exception("feedback_loop_fetch_failed")
        return []
    return [
        {
            "topic": (m.channel_name or m.platform or "general").strip().lower(),
            "question": m.content,
            "source": m.platform,
            "source_event_id": f"{m.channel_id}:{m.thread_id or m.id}",
            "source_url": m.url,
            "org_id": org_id,
        }
        for m in messages
    ]
