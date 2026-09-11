"""Notify agent factory produces a valid Agent with correct constraints."""

from __future__ import annotations

from draftly.agents.notify import build_notify_agent
from draftly.agents.schemas import NotifyReceipt


def test_notify_agent_uses_notify_receipt_schema() -> None:
    from tests.stub_model import StubModel

    model = StubModel(
        structured_outputs={
            NotifyReceipt: {
                "should_notify": True,
                "kind": "gap_detected",
                "body": "Draftly will generate docs for this PR.",
            }
        }
    )
    agent = build_notify_agent(model, [])
    assert agent.name == "pr_notify"
    assert agent._default_structured_output_model is NotifyReceipt


def test_notify_agent_has_no_tools() -> None:
    """Notifying is draft-only; the notify agent must not expose mutation tools."""
    from tests.stub_model import StubModel

    model = StubModel()
    agent = build_notify_agent(model, [])
    assert agent is not None
    assert agent.tool_names == []


def test_notify_agent_returns_structured_receipt() -> None:
    import asyncio

    from tests.stub_model import StubModel

    model = StubModel(
        structured_outputs={
            NotifyReceipt: {
                "should_notify": True,
                "kind": "no_gap",
                "body": "No documentation changes needed for this PR.",
            }
        }
    )
    agent = build_notify_agent(model, [])

    result = asyncio.run(agent.invoke_async("Notify the PR author about this run."))
    receipt = result.structured_output
    assert receipt is not None
    assert receipt.should_notify is True
    assert receipt.kind == "no_gap"