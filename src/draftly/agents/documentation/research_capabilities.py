"""Capability-aware, bounded documentation research (spec: worker-latency Task 6).

A pull_request run almost never needs every researcher. Building them all —
each resolving a model and its connector tooling — burns construction cost and
opens budget on connectors the run cannot use. ``build_research_plan`` decides
deterministically from grounding and connector capabilities which researchers
may exist:

- the grounded repository researcher (``github`` or ``local``) always joins
  GitHub/local PR runs that have the repository capability;
- the documentation researcher joins whenever documentation search is available;
- Slack/Discord are not research connectors on the documentation surface, so
  they are never planned;

A missing *mandatory* capability (repository for a repo-grounded PR, or
documentation) yields a deterministic failed research node instead of a
budget-burning swarm.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_REQUIRES_REPOSITORY_GROUNDING = {"local", "github"}
_RETRYABLE_FAILURE_REASONS = {"authentication", "not_allowed_token_type", "payment_required"}


@dataclass(frozen=True)
class ResearchCapabilities:
    """Connector capabilities available to the run (serialized into grounding)."""

    repository: bool
    documentation: bool

    def to_dict(self) -> dict[str, bool]:
        return {
            "repository": self.repository,
            "documentation": self.documentation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ResearchCapabilities:
        data = data or {}
        return cls(
            repository=bool(data.get("repository")),
            documentation=bool(data.get("documentation")),
        )


@dataclass(frozen=True)
class ResearchPlan:
    """Bounded research plan for one run: which researchers to construct and
    what budget/timeouts the swarm gets (all lowered vs the legacy defaults)."""

    researchers: Sequence[str]
    max_handoffs: int = 3
    max_iterations: int = 6
    execution_timeout: float = 360.0
    node_timeout: float = 180.0
    failure: str | None = None


@dataclass
class ConnectorHealth:
    """Process-local connector failure record (one per ``WorkflowContext``).

    Held by the workflow context — NOT inside a single graph — so an
    authentication/token-type/payment failure suppresses construction of that
    connector for later runs in the same process until cooldown expiry.
    """

    cooldown_seconds: float = 300.0
    unavailable_until: dict[str, float] = field(default_factory=dict)
    diagnosed_by_run: dict[str, set[str]] = field(default_factory=dict)

    def can_attempt(self, connector: str) -> bool:
        return time.monotonic() >= self.unavailable_until.get(connector, 0.0)

    def mark_unavailable(self, connector: str, reason: str, *, run_id: str) -> bool:
        """Record a connector failure; returns True only on the FIRST diagnosis
        for this connector in this run (callers emit exactly one diagnostic
        event per connector per run)."""
        if reason in _RETRYABLE_FAILURE_REASONS:
            self.unavailable_until[connector] = time.monotonic() + self.cooldown_seconds
        diagnosed = self.diagnosed_by_run.setdefault(run_id, set())
        first_diagnostic = connector not in diagnosed
        diagnosed.add(connector)
        return first_diagnostic


def build_research_plan(
    *,
    grounding: str,
    capabilities: ResearchCapabilities,
) -> ResearchPlan:
    """Plan the researchers for one run (deterministic, capability-bounded).

    A missing mandatory capability (repository for a repo-grounded PR, or
    documentation search) returns a plan with ``failure`` set and no
    researchers so the research node fails fast without consuming budget.
    """
    failure = _missing_mandatory_capability(grounding, capabilities)
    if failure is not None:
        return ResearchPlan(researchers=(), failure=failure)

    researchers: list[str] = []
    if grounding in _REQUIRES_REPOSITORY_GROUNDING:
        researchers.append("github" if grounding == "github" else "local")
    if capabilities.documentation:
        researchers.append("docs")

    return ResearchPlan(researchers=tuple(researchers))


def _missing_mandatory_capability(
    grounding: str, capabilities: ResearchCapabilities
) -> str | None:
    if grounding in _REQUIRES_REPOSITORY_GROUNDING and not capabilities.repository:
        return (
            f"missing mandatory research capability 'repository' for "
            f"{grounding} grounding; refusing to research without repository evidence."
        )
    if not capabilities.documentation:
        return (
            "missing mandatory research capability 'documentation'; refusing to "
            "research without documentation-store search."
        )
    return None
