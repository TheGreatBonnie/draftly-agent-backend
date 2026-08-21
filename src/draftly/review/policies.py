"""Review policies (plan §8.3) — always / risky / never.

Delegates resolution to the orchestration policies used by the in-graph
ReviewGate so API-level and graph-level gating agree.
"""

from __future__ import annotations

from draftly.orchestration.routing.policies import (
    is_risky,
    resolve_review_policy,
    should_review,
)

__all__ = ["is_risky", "resolve_review_policy", "should_review"]


class ReviewPolicy:
    """Named policy wrapper for service-level checks."""

    def __init__(self, name: str = "always") -> None:
        self.name = resolve_review_policy(name)

    def requires_review(self, classification: dict | None = None) -> bool:
        return should_review(self.name, classification)
