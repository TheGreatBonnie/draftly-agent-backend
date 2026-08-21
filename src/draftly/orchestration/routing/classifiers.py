"""Deterministic surface classification and workflow routing.

The LLM classifier (``agents.shared.classifier``) interprets an event, but
its output format is not guaranteed — so routing decisions that must be
reliable (surface detection, workflow selection) derive from the normalized
event's ``event_type`` instead. These helpers are the authoritative,
LLM-free mapping used by the event dispatcher and graph builders.
"""

from __future__ import annotations

SURFACE_BY_EVENT_PREFIX = {
    "pull_request": "pull_request",
    "issues": "issue",
    "slack": "support",
    "discord": "support",
}

WORKFLOW_BY_SURFACE = {
    "pull_request": "github_pr",
    "issue": "github_issue",
    "support": "support",
}


def event_prefix(event_type: str) -> str:
    """``"pull_request.opened"`` -> ``"pull_request"``."""
    return str(event_type or "").split(".")[0]


def surface_for_event(event_type: str) -> str | None:
    """Map a normalized event_type to a Draftly surface."""
    return SURFACE_BY_EVENT_PREFIX.get(event_prefix(event_type))


def workflow_for_event(event_type: str) -> str | None:
    """Map a normalized event_type to its workflow/graph surface key."""
    surface = surface_for_event(event_type)
    if surface is None:
        return None
    return WORKFLOW_BY_SURFACE[surface]
