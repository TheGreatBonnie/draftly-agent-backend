"""The writer's read budget must be a correction, not a tool error.

``WriterReadBudget.reserve`` raises ``ValueError``, which strands renders as a
tool error — indistinguishable from a transient GitHub 500. Run d76e2490 made
72 ``github_read_file`` calls against an 8-call budget for exactly that reason,
and the model quoted the message back while still retrying.

A ``Guide`` at ``before_tool_call`` is read as a correction: the tool is
cancelled and the model is told what to do instead.
"""

from __future__ import annotations

import pytest
from strands.hooks.events import BeforeToolCallEvent
from strands.interventions import Guide, Proceed

from draftly.agents.documentation.draft_scope import (
    DraftScope,
    WriterReadBudget,
    set_draft_scope,
)
from draftly.steering.writer_read_budget_guard import WriterReadBudgetGuard

_SCOPE = {
    "run_id": "r1",
    "org_id": "o1",
    "generation": 1,
    "assigned_page_id": "docs/a.md",
    "repository": "o/r",
    "head_sha": "sha",
}


def _scope(budget: WriterReadBudget | None = None) -> DraftScope:
    return DraftScope(
        run_id="r1",
        org_id="o1",
        generation=1,
        assigned_page_id="docs/a.md",
        repository="o/r",
        head_sha="sha",
        read_budget=budget,
    )


def _event(name: str, path: str) -> BeforeToolCallEvent:
    return BeforeToolCallEvent(
        agent=object(),
        selected_tool=None,
        tool_use={"toolUseId": "t1", "name": name, "input": {"path": path}},
        invocation_state={},
    )


@pytest.fixture(autouse=True)
def _no_scope_leak() -> None:
    """Leave the draft-scope ContextVar clean.

    ``set_draft_scope`` publishes into a ContextVar that outlives the test.
    Without this, a scope set here is still set when an unrelated test asserts
    "no active scope" — a real pollution failure, not a flake.
    """
    set_draft_scope(None)
    yield
    set_draft_scope(None)


def _guard_with_scope(budget: WriterReadBudget | None) -> WriterReadBudgetGuard:
    set_draft_scope(_scope(budget))
    return WriterReadBudgetGuard()


def test_first_read_of_a_file_proceeds() -> None:
    guard = _guard_with_scope(WriterReadBudget(max_calls=8))

    assert isinstance(
        guard.before_tool_call(_event("github_read_file", "a.py")), Proceed
    )


def _exhausted_budget(limit: int = 8) -> WriterReadBudget:
    budget = WriterReadBudget(max_calls=limit)
    budget.seen.update(
        {("github_read_file", "o", "r", f"{n}.py", "sha") for n in range(limit)}
    )
    return budget


def test_repeating_a_read_is_guided() -> None:
    """The 72-calls-against-an-8-budget failure."""
    budget = WriterReadBudget(max_calls=8)
    budget.seen.add(("github_read_file", "o", "r", "a.py", "sha"))
    guard = _guard_with_scope(budget)

    action = guard.before_tool_call(_event("github_read_file", "a.py"))

    assert isinstance(action, Guide)
    assert "already read" in action.feedback.lower()


def test_exhausting_the_budget_is_guided() -> None:
    budget = WriterReadBudget(max_calls=2)
    budget.seen.update({("github_read_file", "o", "r", f"{n}.py", "sha") for n in range(2)})
    guard = _guard_with_scope(budget)

    action = guard.before_tool_call(_event("github_read_file", "new.py"))

    assert isinstance(action, Guide)
    assert "budget" in action.feedback.lower()


def test_guidance_tells_the_model_to_draft_not_retry() -> None:
    """A correction, not an error: it must not read as a transient failure."""
    budget = WriterReadBudget(max_calls=8)
    budget.seen.add(("github_read_file", "o", "r", "a.py", "sha"))
    guard = _guard_with_scope(budget)

    action = guard.before_tool_call(_event("github_read_file", "a.py"))

    assert isinstance(action, Guide)
    assert "error" not in action.feedback.lower()
    assert "draft" in action.feedback.lower()


def test_no_scope_is_a_no_op() -> None:
    """Agents without a page assignment keep their existing read behaviour."""
    set_draft_scope(None)
    guard = WriterReadBudgetGuard()

    assert isinstance(guard.before_tool_call(_event("github_read_file", "a.py")), Proceed)


def test_no_budget_in_scope_is_a_no_op() -> None:
    guard = _guard_with_scope(None)

    assert isinstance(guard.before_tool_call(_event("github_read_file", "a.py")), Proceed)


def test_non_read_tool_is_never_guided() -> None:
    """Draft assembly is not a read; it must not consume the read budget."""
    budget = WriterReadBudget(max_calls=1)
    budget.seen.update({("github_read_file", "o", "r", f"{n}.py", "sha") for n in range(8)})
    guard = _guard_with_scope(budget)

    assert isinstance(guard.before_tool_call(_event("append_chunk", "a.py")), Proceed)


def test_each_page_gets_a_fresh_budget() -> None:
    """Two pages must not inherit each other's read budget.

    The budget is owned by ``DraftScope``, which the page-workflow handler
    recreates per task, so the guard holds no counter of its own to reset.
    """
    exhausted = _guard_with_scope(_exhausted_budget())
    assert isinstance(exhausted.before_tool_call(_event("github_read_file", "new.py")), Guide)

    # The next page publishes its own scope with its own budget.
    next_page = _guard_with_scope(WriterReadBudget(max_calls=8))
    assert isinstance(next_page.before_tool_call(_event("github_read_file", "new.py")), Proceed)


def test_missing_input_is_handled() -> None:
    """A malformed tool_use must not raise inside the hook."""
    set_draft_scope(_scope(WriterReadBudget(max_calls=8)))
    guard = WriterReadBudgetGuard()
    event = BeforeToolCallEvent(
        agent=object(),
        selected_tool=None,
        tool_use={"name": "github_read_file"},
        invocation_state={},
    )

    assert isinstance(guard.before_tool_call(event), (Proceed, Guide))
