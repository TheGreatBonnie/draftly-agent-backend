"""Versioned role policies, deterministic checks, limits, and failure matrix."""

from dataclasses import dataclass
from enum import StrEnum

from draftly.steering.decisions import AgentRole, SteeringError


class FailureMode(StrEnum):
    """What happens when a role exhausts its steering budget."""

    INTERRUPT = "interrupt"
    PROCEED = "proceed"


@dataclass(frozen=True)
class SteeringLimits:
    """Bounded automatic-guide budgets shared by every role.

    Values mirror the configuration knobs and are validated at construction
    so a bad config fails fast instead of silently disabling guards.
    """

    tool_guides_per_call: int = 2
    model_guides_per_turn: int = 2
    total_guides_per_agent: int = 5

    def __post_init__(self) -> None:
        for name, value in (
            ("tool_guides_per_call", self.tool_guides_per_call),
            ("model_guides_per_turn", self.model_guides_per_turn),
            ("total_guides_per_agent", self.total_guides_per_agent),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value}")


class PolicyViolation(SteeringError):
    """A deterministic policy rule was violated and no safe fallback exists."""

    def __init__(self, message: str, *, rule: str = "") -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message