"""Steering domain decisions and Strands action adaptation.

This module holds the shared vocabulary used across the steering
subsystem: agent roles, steering phases, the internal decision model,
and the adapter that maps a decision to its Strands action.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AgentRole(StrEnum):
    """Classify the danger surface of a Draftly agent."""

    DELIVERY = "delivery"
    RESEARCH = "research"
    WRITER = "writer"
    REVIEWER = "reviewer"
    CLASSIFIER = "classifier"
    RECOMMENDER = "recommender"
    JUDGE = "judge"
    SUPPORT = "support"


class SteeringPhase(StrEnum):
    """The agent lifecycle point at which steering runs."""

    BEFORE_TOOL = "before_tool"
    AFTER_MODEL = "after_model"


class DecisionKind(StrEnum):
    """The internal steering resolution."""

    PROCEED = "proceed"
    GUIDE = "guide"
    INTERRUPT = "interrupt"


@dataclass(frozen=True)
class SteeringDecision:
    """A resolved steering outcome for one agent lifecycle event."""

    kind: DecisionKind
    phase: SteeringPhase
    reason: str = ""
    role: AgentRole | None = None
    rule: str = ""
    interrupt_id: str | None = None

    @classmethod
    def proceed(
        cls,
        *,
        phase: SteeringPhase,
        reason: str = "",
        role: AgentRole | None = None,
        rule: str = "",
    ) -> "SteeringDecision":
        """Resolve to ``Proceed`` for the given phase."""
        return cls(
            kind=DecisionKind.PROCEED,
            phase=phase,
            reason=_clip_reason(reason),
            role=role,
            rule=rule,
        )

    @classmethod
    def guide(
        cls,
        *,
        phase: SteeringPhase,
        reason: str = "",
        role: AgentRole | None = None,
        rule: str = "",
    ) -> "SteeringDecision":
        """Resolve to ``Guide`` for the given phase."""
        return cls(
            kind=DecisionKind.GUIDE,
            phase=phase,
            reason=_clip_reason(reason),
            role=role,
            rule=rule,
        )

    @classmethod
    def interrupt(
        cls,
        *,
        phase: SteeringPhase,
        reason: str = "",
        role: AgentRole | None = None,
        rule: str = "",
    ) -> "SteeringDecision":
        """Resolve to ``Interrupt`` for a tool-phase event.

        Model steering never interrupts: model judgments are advisory and
        must terminate in ``Proceed`` or ``Guide``.
        """
        if phase is SteeringPhase.AFTER_MODEL:
            raise ValueError("model steering cannot interrupt")
        return cls(
            kind=DecisionKind.INTERRUPT,
            phase=phase,
            reason=_clip_reason(reason),
            role=role,
            rule=rule,
        )

    def to_strands_action(self) -> Any:
        """Map this decision to the matching Strands steering action."""
        from strands.vended_plugins.steering import Guide, Interrupt, Proceed

        if self.kind is DecisionKind.GUIDE:
            return Guide(reason=self.reason)
        if self.kind is DecisionKind.INTERRUPT:
            return Interrupt(reason=self.reason)
        return Proceed(reason=self.reason)


def _clip_reason(reason: str, max_chars: int = 1_000) -> str:
    """Bound reason length so auditing/events stay small and predictable."""
    if reason is None:
        return ""
    return reason[:max_chars]


class SteeringError(Exception):
    """Base class for steering subsystem failures."""


class PolicyViolation(SteeringError):
    """A deterministic policy rule was violated and no safe fallback exists."""


class SteeringFailure(SteeringError):
    """Operational steering failure (persistence, judge, or adapter)."""