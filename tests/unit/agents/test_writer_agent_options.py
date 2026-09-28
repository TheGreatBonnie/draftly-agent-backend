"""The writer builder must honour the options ``WriterFactory`` hands it.

Run ``02b58350`` (PR #63) failed all seven page writes with::

    page_workflow_task_error error="build_writer_agent() got an unexpected
    keyword argument 'session_manager'" error_type=TypeError

``PageWriterHandler`` calls ``writer_factory.create(page,
session_manager=...)`` and ``WriterFactory.create`` documents that
``agent_options`` "are forwarded to the builder, which hands them to the
centralized Strands constructor" -- but ``build_writer_agent`` declared an
explicit keyword-only list with no passthrough, so the contract was broken at
exactly one link in the chain.

The suite missed it because
``test_the_session_manager_reaches_the_writer_agent`` monkeypatches
``create`` with a wrapper that *swallows* the options, proving the handler
passes them without ever reaching the real builder. These tests go through the
real builder.
"""

from __future__ import annotations

from typing import Any

import pytest

from draftly.agents.documentation.writer import WriterFactory, build_writer_agent
from tests.stub_model import StubModel


class _Sessions:
    """A minimal Strands ``SessionManager``.

    Strands registers the session manager as a hook on the agent it is handed
    to (``registry.add_hook`` calls ``register_hooks``), so a bare ``object()``
    is not a faithful double. These tests are about the wiring, not persistence.
    """

    def register_hooks(self, agent: Any, **kwargs: Any) -> None:
        return None


def test_the_real_builder_accepts_a_session_manager() -> None:
    """The exact call the handler makes, against the real builder."""
    manager = _Sessions()

    agent = build_writer_agent(StubModel(), [], session_manager=manager)

    # Strands keeps it private as ``_session_manager``; there is no public
    # accessor, and the wiring is the thing under test.
    assert agent._session_manager is manager


def test_the_factory_reaches_the_real_builder() -> None:
    """The production path end to end: handler option -> factory -> builder."""
    manager = _Sessions()
    factory = WriterFactory(model=StubModel(), tools=[])

    agent = factory.create(task=None, session_manager=manager)  # type: ignore[arg-type]

    assert agent._session_manager is manager


def test_a_page_writer_without_a_session_still_builds() -> None:
    """Callers that pass nothing keep the previous behaviour.

    Strands only sets ``_session_manager`` when one is given, so absence is the
    signal -- not a ``None`` value the builder invented.
    """
    agent = build_writer_agent(StubModel(), [])

    assert getattr(agent, "_session_manager", None) is None


def test_other_agent_options_also_reach_the_constructor() -> None:
    """The contract is a general passthrough, not a one-off ``session_manager``.

    A narrow parameter would leave the next option the handler grows to break
    in exactly the same way, in production, on every page.
    """
    manager = _Sessions()

    agent = build_writer_agent(StubModel(), [], session_manager=manager, record_direct_tool_call=False)

    assert agent.record_direct_tool_call is False


def test_an_unknown_option_is_still_rejected() -> None:
    """A passthrough must not swallow typos into silence.

    ``build_draftly_agent`` gets these straight from the handler, so a silent
    no-op option would be a debugging nightmare.
    """
    with pytest.raises(TypeError):
        build_writer_agent(StubModel(), [], sesion_manager=_Sessions())  # typo


def test_the_writer_still_declares_its_own_output_model() -> None:
    """A caller option must not displace the writer's structured output."""
    agent: Any = build_writer_agent(StubModel(), [])

    assert agent.hooks is not None  # constructed end to end, no duplicate-kwarg blowup
