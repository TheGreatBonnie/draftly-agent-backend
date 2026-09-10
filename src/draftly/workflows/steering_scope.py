"""Per-run steering scope (contextvar) for the graph factory.

``GraphFactory`` is a two-argument ``(run_id, surface)`` callable that the
runner invokes at graph-construction time; unrelated services (tests, manual
calls) inject their own factories. The run's org/project/workflow identity is
threaded through a contextvar — the same pattern as ``workflows.grounding`` —
so the default factory can build ONE SteeringRuntime per run without changing
the calling convention.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

_STEERING_RUN_SCOPE: ContextVar[SteeringRunScope | None] = ContextVar(
    "steering_run_scope", default=None
)


@dataclass(frozen=True)
class SteeringRunScope:
    """Tenant + workflow identity for one run's steering runtime."""

    run_id: str
    surface: str
    org_id: str
    project_id: str
    workflow_key: str = ""


def current_steering_scope() -> SteeringRunScope | None:
    """Return the active run scope, or None outside a graph build."""
    return _STEERING_RUN_SCOPE.get()


def set_steering_scope(scope: SteeringRunScope) -> Token:
    """Bind the run scope; returns a token for :func:`reset_steering_scope`."""
    return _STEERING_RUN_SCOPE.set(scope)


def reset_steering_scope(token: Token) -> None:
    """Restore the previous scope value."""
    _STEERING_RUN_SCOPE.reset(token)
