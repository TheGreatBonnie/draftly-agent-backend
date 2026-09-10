"""Draftly workflow composition (plan §7.3).

Builds the ``WorkflowRegistry`` with every surface and scheduled
workflow, plus the shared ``WorkflowRunner`` used by webhook routes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from .agents import AgentRegistry
from .tools import ToolRegistry

logger = structlog.get_logger(__name__)


def _steering_runtime_factory(
    *,
    repositories: Any,
    config: Any,
) -> Callable[..., Any]:
    """Compose ONE SteeringRuntime per run from config + persistence sinks.

    Config knobs snapshot into a frozen ``SteeringRuntimeConfig``; steering
    attempts/interventions flow through ``SteeringPersistence`` and steering
    audit decisions through ``SteeringAuditSink`` over the run audit repo.
    Missing repositories degrade to ``None`` sinks (policy records without
    persistence) rather than failing the run.
    """
    from draftly.steering.context import SteeringRuntime, SteeringRuntimeConfig
    from draftly.steering.persistence import SteeringAuditSink, SteeringPersistence

    strands = getattr(config, "strands", None)
    attempts_repo = getattr(repositories, "steering_attempts", None)
    interventions_repo = getattr(repositories, "steering_interventions", None)
    audit_repo = getattr(repositories, "agent_runs", None)

    persistence = (
        SteeringPersistence(attempts=attempts_repo, interventions=interventions_repo)
        if attempts_repo is not None and interventions_repo is not None
        else None
    )
    audit = SteeringAuditSink(audit_repo)

    runtime_config = SteeringRuntimeConfig(
        enabled=bool(getattr(strands, "steering_enabled", False)),
        enforcement_enabled=bool(getattr(strands, "steering_enforcement_enabled", False)),
        policy_version=str(getattr(strands, "steering_policy_version", "v1") or "v1"),
        llm_enabled=bool(getattr(strands, "steering_llm_enabled", False)),
        tool_guides_per_call=int(getattr(strands, "steering_tool_guides_per_call", 2) or 2),
        model_guides_per_turn=int(getattr(strands, "steering_model_guides_per_turn", 2) or 2),
        total_guides_per_agent=int(getattr(strands, "steering_total_guides_per_agent", 5) or 5),
        judge_timeout_seconds=(
            float(getattr(strands, "steering_judge_timeout_seconds", 10.0) or 10.0)
        ),
        reason_max_chars=int(getattr(strands, "steering_reason_max_chars", 1_000) or 1_000),
        payload_max_bytes=int(getattr(strands, "steering_payload_max_bytes", 4 * 1024) or 4 * 1024),
    )

    def factory(
        run_id: str,
        surface: str,
        org_id: str,
        project_id: str,
        workflow_key: str | None,
    ) -> SteeringRuntime:
        return SteeringRuntime.from_context(
            run_id=run_id,
            surface=surface,
            org_id=org_id,
            project_id=project_id,
            workflow_key=workflow_key,
            config=runtime_config,
            attempts=persistence,
            audit=audit,
            interventions=persistence,
        )

    return factory


class _TeePublisher:
    """Persist every envelope, then fan out to Redis; never fail the run."""

    def __init__(self, primary: Any, fallback_repo: Any) -> None:
        self.primary = primary
        self.repo = fallback_repo

    async def publish(self, envelope: Any) -> None:
        if self.repo is not None:
            try:
                await self.repo.append(envelope.to_dict())
            except Exception:
                logger.warning("workflow_event_persist_failed", exc_info=True)
        await self.primary.publish(envelope)


@dataclass(frozen=True)
class ComposedWorkflows:
    """The workflow runtime handed to the application lifecycle."""

    registry: Any = None
    runner: Any = None
    context: Any = None
    tasks: dict[str, Any] = field(default_factory=dict)
    event_bus: Any = None


def build_workflows(
    *,
    agents: AgentRegistry | None = None,
    tools: ToolRegistry | None = None,
    repositories: Any = None,
    memory: Any = None,
    evaluation: Any = None,
    feedback: Any = None,
    config: Any = None,
    model: Any = None,
    hooks: list[Any] | None = None,
    audit_repo: Any = None,
    redis_client: Any = None,
    session_repository: Any = None,
) -> ComposedWorkflows:
    """Compose the workflow registry, context, and runner."""
    from draftly.memory.candidates.service import CandidateService
    from draftly.memory.docgraph.service import DocGraphService
    from draftly.memory.episodic.service import EpisodicService
    from draftly.memory.procedural.service import ProceduralService
    from draftly.workflows.content.content_generation import run_content_workflow
    from draftly.workflows.context import WorkflowContext
    from draftly.workflows.documentation.documentation_audit import (
        run_documentation_audit,
    )
    from draftly.workflows.documentation.documentation_sync import (
        run_documentation_sync,
    )
    from draftly.workflows.documentation.github_pr_workflow import (
        run_pull_request_workflow,
    )
    from draftly.workflows.documentation.github_release_workflow import (
        run_release_workflow,
    )
    from draftly.workflows.documentation.stale_reconcile_workflow import (
        run_stale_reconcile,
    )
    from draftly.workflows.evaluation.documentation_evaluation import (
        run_evaluation_loop,
    )
    from draftly.workflows.feedback.documentation_feedback_loop import (
        run_feedback_loop,
    )
    from draftly.workflows.github.feedback_ingestion import ingest_github_feedback
    from draftly.workflows.github.issue_resolution import (
        run_github_issue_workflow,
    )
    from draftly.workflows.maintenance.run_memory_maintenance import (
        run_memory_maintenance,
    )
    from draftly.workflows.memory.curation_workflow import (
        run_memory_curation,
    )
    from draftly.workflows.onboarding.initialize import (
        run_onboarding_initialize,
    )
    from draftly.workflows.registry import WorkflowRegistry
    from draftly.workflows.runner import WorkflowRunner
    from draftly.workflows.support.discord_support_workflow import (
        run_discord_support,
    )
    from draftly.workflows.support.slack_support_workflow import run_slack_support

    context = WorkflowContext(
        repositories=repositories,
        memory=memory,
        evaluation=evaluation,
        feedback=feedback,
        config=config,
        tools=tools,
        agents=agents,
        model=model,
        hooks=list(hooks or []),
        audit_repo=audit_repo,
        storage_dir=getattr(
            getattr(config, "strands", None),
            "session_storage_dir",
            ".draftly/sessions",
        ),
        session_repository=session_repository,
        episodic=EpisodicService(),
        procedural=ProceduralService(),
        docgraph=DocGraphService(),
        candidates=CandidateService(),
        steering_runtime_factory=_steering_runtime_factory(
            repositories=repositories,
            config=config,
        ),
    )

    registry = WorkflowRegistry()
    registry.register("github_pr", run_pull_request_workflow)
    registry.register("github_release", run_release_workflow)
    registry.register("github_issue", run_github_issue_workflow)
    registry.register("github_feedback", ingest_github_feedback)
    registry.register("slack_support", run_slack_support)
    registry.register("discord_support", run_discord_support)
    registry.register("documentation_sync", run_documentation_sync)
    registry.register("documentation_audit", run_documentation_audit)
    registry.register("feedback_loop", run_feedback_loop)
    registry.register("content_generation", run_content_workflow)
    registry.register("evaluation_loop", run_evaluation_loop)
    registry.register("onboarding_initialize", run_onboarding_initialize)
    registry.register("memory_curation", run_memory_curation)
    registry.register("memory_maintenance", run_memory_maintenance)
    registry.register("stale_run_reconcile", run_stale_reconcile)

    publisher = None
    event_bus = None
    if getattr(config, "events_streaming_enabled", False):

        event_bus_mode = getattr(config, "event_bus_backend", "dual")

        if event_bus_mode in ("stream", "dual"):
            from draftly.events.redis_stream_bus import RedisStreamBus

            event_bus = RedisStreamBus(redis_client.native)
        else:
            from draftly.events.redis_bus import RedisEventBus

            event_bus = RedisEventBus(
                redis_client=redis_client.native if redis_client is not None else None,
                url=getattr(config, "redis_url", None) if redis_client is None else None,
            )

        fallback_repo = getattr(repositories, "workflow_events", None)
        publisher = _TeePublisher(event_bus, fallback_repo)
        context.publisher = publisher  # per-surface workflows stream as well

        if redis_client is not None:
            from draftly.events.dashboard_stream_bus import DashboardStreamBus

            if getattr(context, "broadcaster", None) is None:
                context.broadcaster = DashboardStreamBus(redis_client.native)

    runner = WorkflowRunner(context, publisher=publisher)
    context.runner = runner

    notifier = None
    if repositories is not None and getattr(repositories, "reviews", None) is not None:
        from draftly.integrations import email as email_module
        from draftly.integrations.discord.client import DiscordClient
        from draftly.integrations.slack.client import SlackClient
        from draftly.persistence.repositories.review_notifications import (
            ReviewNotificationRepository,
        )
        from draftly.review.notifier import ReviewNotifier

        notifier = ReviewNotifier(
            reviews=getattr(repositories, "reviews", None),
            reviewers=getattr(repositories, "reviewers", None),
            slack=SlackClient(),
            discord=DiscordClient(),
            notifications=ReviewNotificationRepository(),
            email=email_module,
        )
        context.notifier = notifier

    logger.info(
        "workflow registry built workflows=%d streaming=%s",
        len(registry.names()),
        publisher is not None,
    )

    return ComposedWorkflows(
        registry=registry,
        runner=runner,
        context=context,
        event_bus=event_bus,
    )
