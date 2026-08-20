"""
Draftly event composition.

This module is the composition root for event-driven execution.

It connects normalized events to existing Draftly workflows.

Event processors know how to normalize external events.
The EventBus knows how to dispatch events.
This module knows what workflow should respond to each event.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from events.discord_events import DiscordEventProcessor
from events.evaluation_events import EvaluationEventProcessor
from events.event_bus import EventBus, EventEnvelope
from events.github_events import GitHubEventProcessor
from events.scheduler_events import SchedulerEventProcessor
from events.slack_events import SlackEventProcessor

from .workflows import WorkflowRegistry

EventHandler = Callable[
    [EventEnvelope],
    Awaitable[Any],
]


@dataclass(frozen=True)
class EventComposition:
    """
    Complete event runtime.

    Contains the event bus and all external event processors.
    """

    bus: EventBus

    github: GitHubEventProcessor
    slack: SlackEventProcessor
    discord: DiscordEventProcessor
    scheduler: SchedulerEventProcessor
    evaluation: EvaluationEventProcessor


def _workflow_handler(
    workflow: Any,
) -> EventHandler:
    """
    Adapt an existing workflow to the EventBus handler contract.

    The workflow may expose either:

        workflow.run(event)

    or:

        workflow.execute(event)

    The adapter keeps EventBus independent from workflow
    implementation details.
    """

    async def handler(event: EventEnvelope) -> Any:
        # Extract metadata from event
        metadata = event.metadata or {}

        # Build kwargs based on event source
        kwargs: dict[str, Any] = {}
        if event.source == "discord":
            kwargs = {
                "channel_id": metadata.get("channel_id", ""),
                "message_id": metadata.get("message_id", ""),
                "guild_id": metadata.get("guild_id", ""),
                "thread_id": metadata.get("thread_id", ""),
                "question": event.payload.get("message", {}).get("content", ""),
            }
        elif event.source == "slack":
            kwargs = {
                "channel_id": metadata.get("channel_id", ""),
                "thread_ts": metadata.get("thread_ts", ""),
                "question": event.payload.get("text", ""),
            }

        if hasattr(workflow, "run"):
            return await workflow.run(event=event, **kwargs)

        if hasattr(workflow, "execute"):
            return await workflow.execute(event=event, **kwargs)

        raise TypeError(
            f"Workflow {type(workflow).__name__} must expose "
            "`run()` or `execute()`."
        )

    return handler


def register_workflow_handlers(
    *,
    bus: EventBus,
    workflows: WorkflowRegistry,
) -> None:
    """
    Register event → workflow mappings.

    This is intentionally kept outside events/ and workflows/.
    """

    # ---------------------------------------------------------
    # GitHub
    # ---------------------------------------------------------

    bus.subscribe(
        "github.pull_request.opened",
        _workflow_handler(workflows.pull_request),
    )

    bus.subscribe(
        "github.pull_request.updated",
        _workflow_handler(workflows.pull_request),
    )

    bus.subscribe(
        "github.pull_request.closed",
        _workflow_handler(workflows.pull_request),
    )

    bus.subscribe(
        "github.pull_request.merged",
        _workflow_handler(workflows.pull_request),
    )

    bus.subscribe(
        "github.issue.opened",
        _workflow_handler(workflows.issue),
    )

    bus.subscribe(
        "github.issue.updated",
        _workflow_handler(workflows.issue),
    )

    bus.subscribe(
        "github.issue.closed",
        _workflow_handler(workflows.issue),
    )

    bus.subscribe(
        "github.issue.reopened",
        _workflow_handler(workflows.issue),
    )

    bus.subscribe(
        "github.issue_comment.created",
        _workflow_handler(workflows.github_support),
    )

    bus.subscribe(
        "github.release.published",
        _workflow_handler(workflows.release),
    )

    bus.subscribe(
        "github.release.created",
        _workflow_handler(workflows.release),
    )

    bus.subscribe(
        "github.release.changed",
        _workflow_handler(workflows.release),
    )

    bus.subscribe(
        "github.push",
        _workflow_handler(workflows.repository_sync),
    )

    bus.subscribe(
        "github.repository.created",
        _workflow_handler(workflows.repository_sync),
    )

    bus.subscribe(
        "github.repository.updated",
        _workflow_handler(workflows.repository_sync),
    )

    # ---------------------------------------------------------
    # Slack
    # ---------------------------------------------------------

    bus.subscribe(
        "slack.message.received",
        _workflow_handler(workflows.slack_support),
    )

    bus.subscribe(
        "slack.app_mention.received",
        _workflow_handler(workflows.slack_support),
    )

    bus.subscribe(
        "slack.support_question.received",
        _workflow_handler(workflows.slack_support),
    )

    # ---------------------------------------------------------
    # Discord
    # ---------------------------------------------------------

    bus.subscribe(
        "discord.message.received",
        _workflow_handler(workflows.discord_support),
    )

    bus.subscribe(
        "discord.support_question.received",
        _workflow_handler(workflows.discord_support),
    )

    # ---------------------------------------------------------
    # Scheduler
    # ---------------------------------------------------------

    bus.subscribe(
        "scheduler.documentation.health_check",
        _workflow_handler(workflows.daily_health_check),
    )

    bus.subscribe(
        "scheduler.documentation.stale_scan",
        _workflow_handler(workflows.stale_docs_scan),
    )

    bus.subscribe(
        "scheduler.support.gap_scan",
        _workflow_handler(workflows.support_gap_scan),
    )

    bus.subscribe(
        "scheduler.github.release_sync",
        _workflow_handler(workflows.release_sync),
    )

    # ---------------------------------------------------------
    # Evaluation
    # ---------------------------------------------------------

    bus.subscribe(
        "evaluation.completed",
        _workflow_handler(workflows.evaluation_loop),
    )

    bus.subscribe(
        "evaluation.failed",
        _workflow_handler(workflows.failure_recovery),
    )

    bus.subscribe(
        "evaluation.regression_detected",
        _workflow_handler(workflows.regression_loop),
    )

    bus.subscribe(
        "evaluation.improvement_required",
        _workflow_handler(workflows.failure_recovery),
    )


def build_event_system(
    *,
    workflows: WorkflowRegistry,
    bus: EventBus | None = None,
) -> EventComposition:
    """
    Build Draftly's event system.

    Dependency injection is preferred:

        bus = EventBus()
        build_event_system(
            workflows=workflows,
            bus=bus,
        )

    The caller owns the EventBus lifecycle.
    """

    if bus is None:
        bus = EventBus()

    register_workflow_handlers(
        bus=bus,
        workflows=workflows,
    )

    github = GitHubEventProcessor(
        event_bus=bus,
    )

    slack = SlackEventProcessor(
        event_bus=bus,
    )

    discord = DiscordEventProcessor(
        event_bus=bus,
    )

    scheduler = SchedulerEventProcessor(
        event_bus=bus,
    )

    evaluation = EvaluationEventProcessor(
        event_bus=bus,
    )

    return EventComposition(
        bus=bus,
        github=github,
        slack=slack,
        discord=discord,
        scheduler=scheduler,
        evaluation=evaluation,
    )
