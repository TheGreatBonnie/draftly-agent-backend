"""The read-budget guard must be attached to the writer, and must not double-charge.

Two things must hold for the guard to work rather than merely exist:

1. It is in the writer's intervention list.
2. Its key shape matches the tools'. ``reserve_writer_read`` is still called
   inside ``github_read_file``; if the guard and the tool build different keys
   for the same read, one read is charged twice and the budget silently halves.
"""

from __future__ import annotations

import pytest

from draftly.agents.documentation import build_writer_agent
from draftly.agents.documentation.draft_scope import (
    DraftScope,
    WriterReadBudget,
    set_draft_scope,
)
from draftly.steering.writer_read_budget_guard import WriterReadBudgetGuard
from draftly.tools.github.writer_scope import writer_read_key
from tests.stub_model import StubModel


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


def test_writer_agent_carries_the_read_budget_guard() -> None:
    agent = build_writer_agent(StubModel(), [])

    names = {h.name for h in agent._intervention_registry.handlers}
    assert WriterReadBudgetGuard.name in names


def test_tool_and_guard_build_the_same_key() -> None:
    """The tool charges this key; the guard must recognise it as the same read."""
    from draftly.tools.github.writer_scope import reserve_writer_read

    budget = WriterReadBudget(max_calls=8)
    set_draft_scope(
        DraftScope(
            run_id="r1",
            org_id="o1",
            generation=1,
            assigned_page_id="docs/a.md",
            repository="TheGreatBonnie/authly",
            head_sha="abc123",
            read_budget=budget,
        )
    )

    # The tool records a read using its bound arguments, as read_file.py does.
    reserve_writer_read("github_read_file", "TheGreatBonnie", "authly", "a.py", "abc123")

    # The guard must see that exact read as a repeat.
    guard = WriterReadBudgetGuard()
    from strands.hooks.events import BeforeToolCallEvent
    from strands.interventions import Guide

    event = BeforeToolCallEvent(
        agent=object(),
        selected_tool=None,
        tool_use={
            "toolUseId": "t1",
            "name": "github_read_file",
            "input": {"path": "a.py"},  # model omits owner/repo/ref
        },
        invocation_state={},
    )

    assert isinstance(guard.before_tool_call(event), Guide)


def test_a_single_read_is_charged_once() -> None:
    """Two reads, two distinct paths: the budget of 2 must be exactly spent."""
    budget = WriterReadBudget(max_calls=2)
    from draftly.tools.github.writer_scope import reserve_writer_read

    set_draft_scope(
        DraftScope(
            run_id="r1",
            org_id="o1",
            generation=1,
            assigned_page_id="docs/a.md",
            repository="o/r",
            head_sha="sha",
            read_budget=budget,
        )
    )
    reserve_writer_read("github_read_file", "o", "r", "a.py", "sha")
    reserve_writer_read("github_read_file", "o", "r", "b.py", "sha")

    assert len(budget.seen) == 2
    third = writer_read_key("github_read_file", owner="o", repo="r", path="c.py", ref="sha")
    assert budget.failure(third)


@pytest.mark.parametrize("path", ["a.py", "b.py"])
def test_exhausted_budget_reports_failure_for_new_paths(path: str) -> None:
    budget = WriterReadBudget(max_calls=0)

    key = writer_read_key("github_read_file", owner="o", repo="r", path=path, ref="sha")
    assert budget.failure(key)


def test_guard_charges_an_alias_read_to_the_canonical_budget_key() -> None:
    """github_get_file must collide with the github_read_file budget entry.

    The alias tool reserves under the canonical name (Task 1); if the guard
    keyed the alias under its own name, one read would be charged twice and
    the budget would halve.
    """
    from draftly.tools.github.writer_scope import reserve_writer_read

    budget = WriterReadBudget(max_calls=1)
    set_draft_scope(
        DraftScope(
            run_id="r1",
            org_id="o1",
            generation=1,
            assigned_page_id="docs/a.md",
            repository="TheGreatBonnie/authly",
            head_sha="abc123",
            read_budget=budget,
        )
    )
    reserve_writer_read("github_read_file", "TheGreatBonnie", "authly", "a.py", "abc123")

    guard = WriterReadBudgetGuard()
    from strands.hooks.events import BeforeToolCallEvent
    from strands.interventions import Guide

    event = BeforeToolCallEvent(
        agent=object(),
        selected_tool=None,
        tool_use={
            "toolUseId": "t2",
            "name": "github_get_file",
            "input": {"path": "a.py"},
        },
        invocation_state={},
    )

    assert isinstance(guard.before_tool_call(event), Guide)
