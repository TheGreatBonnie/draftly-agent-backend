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
from dataclasses import dataclass, field


@dataclass
class WriterReadBudget:
    """Per-page limit on distinct GitHub reads across one writer invocation."""

    max_calls: int = 8
    seen: set[tuple[str, ...]] = field(default_factory=set)

    def failure(self, key: tuple[str, ...]) -> str | None:
        if key in self.seen:
            return "This source was already read; use its existing evidence and draft the page."
        if len(self.seen) >= self.max_calls:
            return (
                f"Writer read budget of {self.max_calls} calls exhausted; "
                "draft the page from gathered evidence."
            )
        return None

    def reserve(self, key: tuple[str, ...]) -> None:
        reason = self.failure(key)
        if reason:
            raise ValueError(reason)
        self.seen.add(key)


@dataclass
class DraftProgress:
    """Successful draft tool calls in one page writer invocation."""

    draft_id: str | None = None
    chunks: int = 0
    sealed: bool = False


@dataclass(frozen=True)
class DraftScope:
    """Resolved run + tenant + generation for a writer's draft tools."""

    run_id: str
    org_id: str
    #: Generation the current writer node execution opens (see NextGenerationHook).
    generation: int
    #: Durable page artifact version. Legacy graph writers leave this unset.
    version: int | None = None
    #: Canonical page path assigned to a page-workflow writer. Legacy scopes omit it.
    assigned_page_id: str | None = None
    #: Trusted source repository and PR commit for a page-workflow writer.
    repository: str | None = None
    head_sha: str | None = None
    read_budget: WriterReadBudget | None = None
    progress: DraftProgress | None = None


_draft_scope: ContextVar[DraftScope | None] = ContextVar("draftly_draft_scope", default=None)


def set_draft_scope(scope: DraftScope | None) -> Token[DraftScope | None]:
    """Set the active draft scope; returns the reset token."""
    return _draft_scope.set(scope)


def reset_draft_scope(token: Token[DraftScope | None]) -> None:
    _draft_scope.reset(token)


def current_draft_scope() -> DraftScope | None:
    return _draft_scope.get()
