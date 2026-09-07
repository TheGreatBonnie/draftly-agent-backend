"""Event types routed through the Draftly pipeline (plan §7.1).

The string values are the canonical ``event_type`` prefixes used by the
deterministic classifiers (``draftly.orchestration.routing.classifiers``)
and by ``EventDispatcher.route``.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    """Every event surface Draftly consumes or emits."""

    GITHUB_PULL_REQUEST = "pull_request"
    GITHUB_ISSUE = "issues"
    GITHUB_RELEASE = "release"
    GITHUB_PUSH = "push"
    GITHUB_ISSUE_COMMENT = "issue_comment"
    GITHUB_PULL_REQUEST_REVIEW = "pull_request_review"
    GITHUB_PULL_REQUEST_REVIEW_COMMENT = "pull_request_review_comment"
    SLACK_SUPPORT = "slack"
    DISCORD_SUPPORT = "discord"
    DOCUMENTATION_CHANGED = "documentation.changed"
    DOCUMENTATION_PUBLISHED = "documentation.published"
    REVIEW_COMPLETED = "review.completed"


# Canonical graph surfaces (build_graph_for_run's ``surface`` argument).
SURFACE_PULL_REQUEST = "pull_request"
SURFACE_ISSUE = "issue"
SURFACE_SUPPORT = "support"
SURFACE_CONTENT = "content"

SURFACE_BY_EVENT_TYPE: dict[EventType, str] = {
    EventType.GITHUB_PULL_REQUEST: SURFACE_PULL_REQUEST,
    EventType.GITHUB_ISSUE: SURFACE_ISSUE,
    EventType.GITHUB_RELEASE: SURFACE_PULL_REQUEST,
    EventType.GITHUB_PUSH: SURFACE_PULL_REQUEST,
    EventType.SLACK_SUPPORT: SURFACE_SUPPORT,
    EventType.DISCORD_SUPPORT: SURFACE_SUPPORT,
}
