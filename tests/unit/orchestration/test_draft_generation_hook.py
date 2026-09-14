"""NextGenerationHook assigns an immutable draft generation per writer run.

The DraftScope contextvar gets the generation before each writer node fires,
so its start_draft/append_chunk tools address the store with the right
generation. Seed from invocation_state's ``draft_generation_seed`` (runner
computes MAX(generation)+1) keeps resume-after-review correct.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from draftly.agents.documentation.draft_scope import (
    current_draft_scope,
    reset_draft_scope,
    set_draft_scope,
)
from draftly.orchestration.hooks.draft_generation import NextGenerationHook

WRITER_NODE_IDS = ("answer", "update", "create")


def _event(node_id: str, state: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id,
        invocation_state=state
        or {"run_id": "run-1", "project_id": "org-1", "surface": "pull_request"},
        source=SimpleNamespace(state={}),
    )


@pytest.fixture(autouse=True)
def _clear_scope() -> None:
    scope = current_draft_scope()
    token = set_draft_scope(None)
    yield
    reset_draft_scope(token)
    if scope is not None:
        set_draft_scope(scope)


def test_writer_node_start_sets_scope_with_next_generation() -> None:
    hook = NextGenerationHook()
    hook._on_node_start(_event("update"))
    scope = current_draft_scope()
    assert scope is not None
    assert scope.run_id == "run-1"
    assert scope.generation == 1

    hook._on_node_start(_event("create"))
    assert current_draft_scope().generation == 2


def test_non_writer_nodes_leave_scope_untouched() -> None:
    hook = NextGenerationHook()
    hook._on_node_start(_event("update"))
    token = set_draft_scope(None)
    try:
        hook._on_node_start(_event("context"))
        assert current_draft_scope() is None
    finally:
        reset_draft_scope(token)


def test_deliver_node_publishes_scope_without_advancing_generation() -> None:
    """Delivery reads the sealed draft store via get_drafted_docs, so it needs
    a run-scoped DraftScope but must NOT open a new generation."""
    hook = NextGenerationHook()
    hook._on_node_start(_event("update"))
    assert current_draft_scope().generation == 1

    hook._on_node_start(_event("deliver"))
    scope = current_draft_scope()
    assert scope is not None
    assert scope.run_id == "run-1"
    assert scope.org_id == "org-1"
    assert scope.generation == 1

    hook._on_node_start(_event("update"))
    assert current_draft_scope().generation == 2


def test_missing_run_id_is_a_noop() -> None:
    hook = NextGenerationHook()
    hook._on_node_start(_event("update", {"project_id": "org-1"}))
    assert current_draft_scope() is None


def test_seed_makes_generations_resume_safe() -> None:
    """Existing sealed generations (MAX(generation)=3) continue at 4 on resume."""
    hook = NextGenerationHook()
    seed_state = {"run_id": "run-1", "project_id": "org-1", "draft_generation_seed": 4}
    hook._on_node_start(_event("update", seed_state))
    assert current_draft_scope().generation == 4
    hook._on_node_start(_event("create", seed_state))
    assert current_draft_scope().generation == 5


def test_per_run_counters_are_isolated() -> None:
    hook = NextGenerationHook()
    hook._on_node_start(_event("update"))
    hook._on_node_start(_event("update", {"run_id": "run-2", "project_id": "org-2"}))
    assert current_draft_scope().run_id == "run-2"
    assert current_draft_scope().generation == 1


def test_register_hooks_binds_before_node_callback() -> None:
    hook = NextGenerationHook()
    captured: list[str] = []

    class _Registry:
        def add_callback(self, event_type: Any, callback: Any) -> None:
            captured.append(event_type.__name__)
            hook._on_node_start = callback

    hook.register_hooks(_Registry())
    assert captured == ["BeforeNodeCallEvent"]
    hook._on_node_start(_event("update"))
    assert current_draft_scope().generation == 1
