"""Run-scoped draft store scope for documentation writer tools.

Mirrors ``memory/scope.py``: the runner knows the run id, org id, and the
current draft generation (from the graph's per-node ``NextGenerationHook``)
and publishes a ``DraftScope`` here for the duration of a writer invocation.
The writer tools read it at call time to address the draft store without
threading run metadata through every tool signature — and, critically, never
through the model output path.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class DraftScope:
    """Resolved run + tenant + generation for a writer's draft tools."""

    run_id: str
    org_id: str
    #: Generation the current writer node execution opens (see NextGenerationHook).
    generation: int


_draft_scope: ContextVar[DraftScope | None] = ContextVar(
    "draftly_draft_scope", default=None
)


def set_draft_scope(scope: DraftScope | None) -> Token[DraftScope | None]:
    """Set the active draft scope; returns the reset token."""
    return _draft_scope.set(scope)


def reset_draft_scope(token: Token[DraftScope | None]) -> None:
    _draft_scope.reset(token)


def current_draft_scope() -> DraftScope | None:
    return _draft_scope.get()
