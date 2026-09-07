"""Event dispatcher: EventType → workflow function (plan §7.1).

Two routing jobs:
- ``route(event)`` → graph surface string for ``build_graph_for_run``
  (delegates to the deterministic Phase 4 classifiers),
- ``dispatch(event)`` → invoke the registered workflow function for the
  event's type prefix.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from draftly.events.types import (
    SURFACE_BY_EVENT_TYPE,
    SURFACE_CONTENT,
    SURFACE_ISSUE,
    SURFACE_PULL_REQUEST,
    SURFACE_SUPPORT,
    EventType,
)
from draftly.orchestration.routing.classifiers import event_prefix

logger = structlog.get_logger(__name__)

WorkflowFunc = Callable[..., Awaitable[Any]]

# Deterministic prefix → surface mapping (mirrors the graph classifiers
# so dispatcher and graphs can never disagree).
SURFACE_BY_PREFIX: dict[str, str] = {
    EventType.GITHUB_PULL_REQUEST.value: SURFACE_PULL_REQUEST,
    EventType.GITHUB_RELEASE.value: SURFACE_PULL_REQUEST,
    EventType.GITHUB_PUSH.value: SURFACE_PULL_REQUEST,
    EventType.GITHUB_ISSUE.value: SURFACE_ISSUE,
    EventType.SLACK_SUPPORT.value: SURFACE_SUPPORT,
    EventType.DISCORD_SUPPORT.value: SURFACE_SUPPORT,
    "content": SURFACE_CONTENT,
}


class EventDispatcher:
    """Routes normalized events to workflows and graph surfaces."""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowFunc] = {}

    def register(self, event_type: str | EventType, workflow: WorkflowFunc) -> None:
        """Register a workflow function under an event-type prefix."""
        key = self._prefix(event_type)
        self._workflows[key] = workflow

    def workflow_for(self, event_type: str | EventType) -> WorkflowFunc | None:
        return self._workflows.get(self._prefix(event_type))

    def route(self, event: dict[str, Any]) -> str | None:
        """Map a normalized event to its graph surface (None if unknown)."""
        body = event.get("release") or event.get("pull_request") or {}
        if event.get("content_relevant") or body.get("content_relevant"):
            return "content"
        event_type = str(event.get("event_type", ""))
        prefix = event_prefix(event_type)
        if prefix in SURFACE_BY_PREFIX:
            return SURFACE_BY_PREFIX[prefix]
        # Fall back to the shared classifiers (keeps one source of truth).
        from draftly.orchestration.routing.classifiers import surface_for_event

        return surface_for_event(event_type)

    async def dispatch(self, event: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        """Invoke the registered workflow for this event, if any."""
        workflow = self.workflow_for(str(event.get("event_type", "")))
        if workflow is None:
            logger.info(
                "dispatcher_no_workflow event_type=%s",
                event.get("event_type"),
            )
            return None
        return await workflow(event, *args, **kwargs)

    @staticmethod
    def _prefix(event_type: str | EventType) -> str:
        value = event_type.value if isinstance(event_type, EventType) else str(event_type)
        return event_prefix(value)


__all__ = [
    "SURFACE_BY_EVENT_TYPE",
    "SURFACE_BY_PREFIX",
    "EventDispatcher",
    "WorkflowFunc",
]
