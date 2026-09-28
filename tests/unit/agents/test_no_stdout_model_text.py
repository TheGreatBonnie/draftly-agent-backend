"""Application agents stream model text to stdout.

REVERSED. This file previously asserted the opposite, and the reversal is
deliberate.

Strands' default ``callback_handler`` is ``PrintingCallbackHandler``, which
``print()``s every reasoning delta, every text delta, and a ``Tool #N:`` line
per tool block straight to stdout. ``b662209`` set ``callback_handler=None`` in
``build_draftly_agent`` to silence it, because run d76e2490 produced 2,242 raw
text lines and 65,520 ``<unk>`` tokens that interleaved with structlog on the
same fd and made the last ~8 minutes of the failing ``document`` node
unreadable in a rich console.

That suppression was reverted: reasoning is wanted in the worker log. The
d76e2490 cost is volume, not corruption, and it is now paid knowingly. The
``show_locals=False`` formatter from ``00537a6`` also removed the traceback
render that made that same run 97% of its bytes.

The tests are inverted rather than deleted, so streaming cannot silently
regress. The judge stays silenced -- see
``test_judge_agent_remains_silenced``.
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
    # No ``text``: the writer is a structured-output agent, so StubModel emits
    # a DocChangePlan tool call and never a text delta.
    return _ReasoningStubModel(
        structured_outputs={
            DocChangePlan: {"files": [{"path": "docs/a.md", "action": "create"}]},
        },
    )


@pytest.mark.asyncio
async def test_writer_agent_streams_model_text_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reasoning model's deltas must reach stdout, or reasoning is not logged.

    Only two of the three handler outputs apply here. The writer is a
    structured-output agent: its answer arrives as a ``DocChangePlan`` tool
    call, so ``StubModel`` never emits a text delta and there is no
    ``VISIBLE-ANSWER-TEXT`` line to assert. Reasoning and the ``Tool #N:``
    line are the two that matter.
    """
    agent = build_writer_agent(_reasoning_model(), [])

    assert isinstance(agent, Agent)
    await agent.invoke_async("write the page")

    captured = capsys.readouterr()
    assert _REASONING in captured.out
    assert "Tool #1: DocChangePlan" in captured.out


def test_writer_agent_uses_printing_callback_handler() -> None:
    """Pin the root cause so the default cannot silently disappear again."""
    from strands.handlers.callback_handler import PrintingCallbackHandler

    agent = build_writer_agent(StubModel(), [])

    assert isinstance(agent.callback_handler, PrintingCallbackHandler)


def test_judge_agent_remains_silenced() -> None:
    """The judge is a deliberate exception, not a leftover.

    It is no longer "consistent" with the writer -- that consistency is
    deliberately broken. The judge is built outside ``build_draftly_agent`` and
    stays silent because its reasoning is long, repetitive grading commentary
    on each of the 242 judge round-trips of d76e2490: volume with no
    diagnostic value.
    """
    from strands.handlers.callback_handler import PrintingCallbackHandler

    from draftly.steering.handler import _IsolatedJudge

    judge = _IsolatedJudge(system_prompt="judge", model=StubModel())

    assert not isinstance(judge._agent.callback_handler, PrintingCallbackHandler)
