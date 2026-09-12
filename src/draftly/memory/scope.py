"""Run-scoped memory search scope.

Real documentation entries live in the ``documents`` namespace under the
org's id, but the LLM cannot know that vocabulary and guesses namespaces
("authly", project ids, ...), so searches come back empty. The runner knows
both the org (from ``event.project_id``) and the canonical docs namespace,
and publishes a ``MemoryScope`` here for the duration of the invocation.

Search tools read the active scope at call time:
- when the scope's namespace is set, it wins over the LLM-supplied one;
- the scope's ``org_id`` constrains every query to the tenant, closing the
  cross-org leak that keyword search previously had.

The scope lives in this leaf module (mirroring ``github/runtime.py`` and
``support/runtime.py``) and stays out of the prompt entirely.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

# Documentation surfaces search the canonical docs store; other surfaces
# (support, ...) keep the LLM-supplied namespace.
_DOCUMENTATION_SURFACES = ("pull_request", "issue", "content")


@dataclass(frozen=True)
class MemoryScope:
    """Resolved tenant + namespace for a run's memory searches."""

    org_id: str
    namespace: str | None = None


_memory_scope: ContextVar[MemoryScope | None] = ContextVar("draftly_memory_scope", default=None)


def set_memory_scope(scope: MemoryScope | None) -> Token[MemoryScope | None]:
    """Set the active memory scope; returns the reset token."""
    return _memory_scope.set(scope)


def reset_memory_scope(token: Token[MemoryScope | None]) -> None:
    _memory_scope.reset(token)


def current_memory_scope() -> MemoryScope | None:
    return _memory_scope.get()


def _documents_namespace() -> str:
    from draftly.memory.repository import MemoryNamespaces

    return MemoryNamespaces.DOCUMENTS


def memory_scope_for(event: dict[str, Any], surface: str) -> MemoryScope | None:
    """Build a run's memory scope from a normalized event, else ``None``.

    Scope resolution guarantees org isolation for every surface, and pins the
    canonical docs namespace for documentation surfaces.
    """
    org_id = str(event.get("project_id") or event.get("org_id") or "")
    if not org_id:
        return None
    namespace: str | None = None
    if surface in _DOCUMENTATION_SURFACES:
        namespace = _documents_namespace()
    return MemoryScope(org_id=org_id, namespace=namespace)
