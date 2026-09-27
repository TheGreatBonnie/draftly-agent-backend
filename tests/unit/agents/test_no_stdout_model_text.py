"""Application agents must not print model text to stdout.

Strands' default ``callback_handler`` is ``PrintingCallbackHandler``, which
``print()``s every reasoning delta, every text delta, and a ``Tool #N:`` line
per tool block straight to stdout. ``build_draftly_agent`` never passed
``callback_handler``, so every Draftly agent inherited it and the model text
interleaved with structlog on the same fd.

Run d76e2490 produced 2,242 raw text lines and 65,520 ``<unk>`` tokens that way,
spliced into surrounding log records and making the last ~8 minutes of the
failing ``document`` node unreadable.

These tests assert the observable behaviour (nothing reaches stdout) rather than
the constructor kwarg, so a future refactor cannot pass them by accident.
"""

from __future__ import annotations

import pytest
from strands import Agent

from draftly.agents.documentation import build_writer_agent
from draftly.agents.schemas import DocChangePlan
from tests.stub_model import StubModel

_REASONING = "SECRET-CHAIN-OF-THOUGHT"


class _ReasoningStubModel(StubModel):
    """Streams reasoning and text deltas, as a reasoning model does.

    The writer is a structured-output agent, so the base class emits the
    scripted ``DocChangePlan`` tool call. This subclass then appends the
    thinking phase a reasoning model produces before answering: the callback
    handler prints those deltas, and nothing else does.
    """

    async def stream(  # type: ignore[override]
        self, messages, tool_specs=None, system_prompt=None, **kwargs
    ):
        # A reasoning model emits a thinking phase before its answer, in the
        # start/delta/stop triple strands' OpenAI model produces. The callback
        # handler prints the delta; nothing else does.
        yield {"contentBlockStart": {"start": {}}}
        yield {
            "contentBlockDelta": {"delta": {"reasoningContent": {"text": _REASONING}}}
        }
        yield {"contentBlockStop": {}}
        async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
            yield event


def _reasoning_model() -> _ReasoningStubModel:
    return _ReasoningStubModel(
        text="VISIBLE-ANSWER-TEXT",
        structured_outputs={
            DocChangePlan: {"files": [{"path": "docs/a.md", "action": "create"}]},
        },
    )


@pytest.mark.asyncio
async def test_writer_agent_emits_no_model_text_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reasoning model's deltas must not reach stdout."""
    agent = build_writer_agent(_reasoning_model(), [])

    assert isinstance(agent, Agent)
    await agent.invoke_async("write the page")

    captured = capsys.readouterr()
    assert "SECRET-CHAIN-OF-THOUGHT" not in captured.out
    assert "VISIBLE-ANSWER-TEXT" not in captured.out
    assert "Tool #" not in captured.out


def test_writer_agent_does_not_use_printing_callback_handler() -> None:
    """Pin the root cause so the default cannot silently return."""
    from strands.handlers.callback_handler import PrintingCallbackHandler

    agent = build_writer_agent(StubModel(), [])

    assert not isinstance(agent.callback_handler, PrintingCallbackHandler)


def test_judge_agent_remains_silenced() -> None:
    """The judge was already silenced; keep both agents consistent."""
    from strands.handlers.callback_handler import PrintingCallbackHandler

    from draftly.steering.handler import _IsolatedJudge

    judge = _IsolatedJudge(system_prompt="judge", model=StubModel())

    assert not isinstance(judge._agent.callback_handler, PrintingCallbackHandler)
