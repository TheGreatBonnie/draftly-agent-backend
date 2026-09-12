"""Run-scoped memory search scope: org + namespace for search tools.

Real documentation entries live in the ``documents`` namespace under the
org's id, but the LLM cannot know that vocabulary, so it guesses namespaces
("authly", project ids, ...) and every search comes back empty. The run
context knows both the org and the canonical docs namespace; search tools
must honor it when present.
"""

from __future__ import annotations

import draftly.feedback  # noqa: F401  (resolves the memory package import order)
from draftly.memory.scope import (
    MemoryScope,
    current_memory_scope,
    memory_scope_for,
    reset_memory_scope,
    set_memory_scope,
)


def test_memory_scope_for_documentation_surface_uses_documents_namespace() -> None:
    scope = memory_scope_for(
        {"project_id": "org-123", "event_type": "pull_request.opened"},
        "pull_request",
    )
    assert scope is not None
    assert scope.org_id == "org-123"
    assert scope.namespace == "documents"


def test_memory_scope_for_support_surface_filters_org_only() -> None:
    scope = memory_scope_for(
        {"project_id": "org-123", "event_type": "message.im"},
        "slack",
    )
    assert scope is not None
    assert scope.org_id == "org-123"
    assert scope.namespace is None


def test_memory_scope_for_event_without_org_is_none() -> None:
    assert memory_scope_for({"event_type": "pull_request.opened"}, "pull_request") is None


def test_memory_scope_context_round_trip() -> None:
    token = set_memory_scope(MemoryScope(org_id="org-1", namespace="documents"))
    try:
        assert current_memory_scope() == MemoryScope(org_id="org-1", namespace="documents")
    finally:
        reset_memory_scope(token)
    assert current_memory_scope() is None
