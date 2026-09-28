"""A ``max_tokens`` response must be able to fail over.

Run ``ce8ea540`` (PR #62): ``requesty`` returned 402, failover worked, and
``nemotron-ultra-doc`` then returned a *complete* stream flagged
``stop_reason == "max_tokens"``. Strands raises ``MaxTokensReachedException``
in the event loop at ``event_loop.py:310`` -- after the model generator has
already finished normally -- so ``PaymentAwareModel`` never sees an exception
and never gets to rotate. The node died.

The wrapper only sees the terminating event, so that is where the signal has to
be read. It is ``{"messageStop": {"stopReason": "max_tokens"}}`` -- Strands
normalises every provider's finish reason into that one shape (OpenAI's
``length``, Anthropic's ``max_tokens``, Bedrock's, ...). This was wrong the
first time: the predicate matched ``{"stop": (..., stop_reason)}``, which no
Strands model yields, so the failover was dead code. Run ``9ab7a0a0`` proved it
-- the ``impact`` node died on ``max_tokens`` with zero ``model_failover`` log
lines despite six enabled providers.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from strands.models import OpenAIModel
from strands.types.exceptions import MaxTokensReachedException

from draftly.integrations.strands.models import PaymentAwareModel


class _Health:
    """Minimal provider-health double recording what the wrapper did."""

    def __init__(self) -> None:
        self.disabled = False
        self.failures: list[str] = []

    def disable(self) -> None:
        self.disabled = True

    def record_failure(self, failure: str) -> None:
        self.failures.append(failure)


class _Registry:
    def __init__(self, fallback_stop_reason: str = "end_turn") -> None:
        self._fallback_stop_reason = fallback_stop_reason

    def get_model(self, name: str) -> Any:
        return SimpleNamespace(name=name)

    def get_provider(self, name: str) -> Any:
        return SimpleNamespace(
            create_model=lambda config: _model_ending_with(self._fallback_stop_reason)
        )


class _Router:
    def __init__(self, health: _Health, fallback_stop_reason: str = "end_turn") -> None:
        self.health = self
        self.registry = _Registry(fallback_stop_reason)
        self._health = health
        self.calls: list[str] = []

    def get(self, provider: str) -> _Health:
        return self._health

    def route(self, request: Any = None, **kwargs: Any) -> Any:
        self.calls.append(getattr(request, "task_type", None) or "?")
        return SimpleNamespace(selected_model="fallback-model", provider="fallback-provider")


#: Provider ``finish_reason`` -> the stop reason Strands normalises it to.
#: ``length`` is the OpenAI-compatible spelling of a token-limit truncation;
#: ``tool_calls`` is a normal tool-calling turn; anything else is ``end_turn``.
_FINISH_REASON = {"max_tokens": "length", "tool_use": "tool_calls"}


def _terminal_event(stop_reason: str) -> dict[str, Any]:
    r"""The terminating event, produced by Strands' own formatter.

    Built by calling ``OpenAIModel.format_chunk`` rather than by hand-writing
    the dict. The previous version of this file hand-wrote
    ``{"stop": (None, {...}, stop_reason)}`` -- a shape that **no Strands model
    emits** (``grep -rE 'yield .*\{"stop"' strands/models/*.py`` is empty; the
    only ``event["stop"]`` is a *read* of a boto3 Bedrock event). So the tests
    passed against a fiction and the real truncation never rotated. Deriving the
    event from Strands means a future upstream change shows up as a test failure
    instead of as a production node death.
    """
    finish = _FINISH_REASON.get(stop_reason, stop_reason)
    return OpenAIModel.format_chunk(None, {"chunk_type": "message_stop", "data": finish})


def _model_ending_with(stop_reason: str) -> Any:
    """A model whose single response terminates with ``stop_reason``.

    Implements both ``stream`` and ``structured_output`` because
    ``PaymentAwareModel`` wraps both and cannot know which the agent will call.
    """

    class _M:
        def __init__(self) -> None:
            self.inner = self

        async def stream(self, *args: Any, **kwargs: Any) -> Any:
            yield {"messageStart": {"role": "assistant"}}
            yield _terminal_event(stop_reason)

        async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
            yield _terminal_event(stop_reason)

    return _M()


def _wrapper(
    stop_reason: str,
    max_failovers: int | None = None,
    fallback_stop_reason: str = "end_turn",
) -> tuple[PaymentAwareModel, _Router]:
    health = _Health()
    router = _Router(health, fallback_stop_reason)
    model = PaymentAwareModel(
        _model_ending_with(stop_reason),
        router=router,
        role="context",
        provider="nebius_token_factory",
    )
    if max_failovers is not None:
        model._MAX_FAILOVERS = max_failovers
    return model, router


async def _drain(model: PaymentAwareModel) -> list[Any]:
    return [event async for event in model.structured_output("task")]


@pytest.mark.asyncio
async def test_max_tokens_stop_rotates_the_provider() -> None:
    """The whole point: a truncated response must not kill the node."""
    model, router = _wrapper("max_tokens")

    await _drain(model)

    assert router.calls, "provider was never re-resolved after max_tokens"


@pytest.mark.asyncio
async def test_max_tokens_marks_the_provider_unhealthy() -> None:
    """A too-small provider must not be handed the same role again.

    Without this, the next node resolves the same model and truncates again,
    so one truncation becomes a run-long pattern instead of one rotated call.
    """
    health = _Health()
    router = _Router(health)
    model = PaymentAwareModel(
        _model_ending_with("max_tokens"),
        router=router,
        role="context",
        provider="nebius_token_factory",
    )

    await _drain(model)

    assert health.failures or health.disabled


@pytest.mark.asyncio
async def test_normal_stop_does_not_rotate() -> None:
    model, router = _wrapper("end_turn")

    await _drain(model)

    assert router.calls == []


@pytest.mark.asyncio
async def test_tool_use_stop_does_not_rotate() -> None:
    """A tool-calling turn is normal; it must not cost a failover."""
    model, router = _wrapper("tool_use")

    await _drain(model)

    assert router.calls == []


@pytest.mark.asyncio
async def test_rotation_is_bounded_then_reraises() -> None:
    """Every provider truncating must eventually fail, not loop forever."""
    model, router = _wrapper("max_tokens", max_failovers=2, fallback_stop_reason="max_tokens")

    with pytest.raises(MaxTokensReachedException):
        await _drain(model)

    assert len(router.calls) == 2


@pytest.mark.asyncio
async def test_a_working_fallback_stops_the_rotation() -> None:
    """One rotation is enough when the next provider answers cleanly."""
    model, router = _wrapper("max_tokens", fallback_stop_reason="end_turn")

    await _drain(model)

    assert len(router.calls) == 1


# --- the event shape is the whole bug -------------------------------------
#
# The first version of this file asserted against a hand-written event that
# Strands never emits, so all six tests were green while the failover did
# nothing. These pin the shape itself.


def test_the_terminal_event_is_the_one_strands_emits() -> None:
    """Guard the premise: this is Strands' real normalising formatter."""
    event = _terminal_event("max_tokens")

    assert event == {"messageStop": {"stopReason": "max_tokens"}}


def test_the_predicate_reads_strands_event_not_a_legacy_shape() -> None:
    """The old ``{"stop": (...)}`` shape must no longer be what we match.

    Asserted negatively on purpose: if a future edit "helpfully" restores
    support for the invented shape *instead of* the real one, this fails.
    """
    from draftly.integrations.strands.models import _is_max_tokens_event

    assert not _is_max_tokens_event({"stop": (None, {}, "max_tokens")})


@pytest.mark.parametrize(
    "event",
    [
        None,
        "max_tokens",
        {},
        {"messageStop": None},
        {"messageStop": {}},
        {"messageStop": {"stopReason": None}},
        {"messageStop": {"stop_reason": "max_tokens"}},
        {"contentBlockStop": {}},
    ],
    ids=[
        "none",
        "bare-string",
        "empty",
        "null-stop",
        "empty-stop",
        "null-reason",
        "snake-case-is-not-strands",
        "content-block-stop",
    ],
)
def test_malformed_events_are_not_truncation(event: Any) -> None:
    """Only the exact terminal shape counts; everything else is 'no signal'."""
    from draftly.integrations.strands.models import _is_max_tokens_event

    assert not _is_max_tokens_event(event)


@pytest.mark.asyncio
async def test_the_stream_path_rotates() -> None:
    """``stream`` is the path the event loop uses, so it is the one that matters.

    ``structured_output`` is non-streaming in Strands and yields only
    ``{"output": ...}``; every other test here drives that method, so without
    this one the production path would be untested.
    """
    model, router = _wrapper("max_tokens")

    events = [event async for event in model.stream("task")]

    assert router.calls, "stream() truncation did not rotate the provider"
    # The truncation event is still forwarded, not swallowed: the retry needs
    # the fresh stream, and swallowing would hide the rotation from the caller.
    assert events[-1] == {"messageStop": {"stopReason": "end_turn"}}


@pytest.mark.asyncio
async def test_a_clean_stream_does_not_rotate() -> None:
    model, router = _wrapper("end_turn")

    async for _ in model.stream("task"):
        pass

    assert router.calls == []


# --- the label an operator reads ------------------------------------------


def test_a_truncation_is_not_labelled_a_timeout() -> None:
    """``_classify_failure`` defaults unrecognised errors to ``FAILURE_TIMEOUT``.

    ``MaxTokensReachedException("max_tokens")`` fell into that default, so the
    log an operator reads when this fires in production says
    ``model_failover ... failure=timeout`` -- sending them to chase network
    latency when the real cause is a per-response output cap.
    """
    from draftly.models.health import FAILURE_RATE_LIMIT
    from draftly.models.router import ModelRouter

    assert ModelRouter._classify_failure(MaxTokensReachedException("max_tokens")) == (
        FAILURE_RATE_LIMIT
    )


@pytest.mark.asyncio
async def test_a_truncation_does_not_disable_the_provider() -> None:
    """Too small for one call is not an outage.

    Disabling would take the provider out of rotation permanently for what is a
    per-request capacity problem; a cooldown lets it back in.
    """
    health = _Health()
    router = _Router(health)
    model = PaymentAwareModel(
        _model_ending_with("max_tokens"),
        router=router,
        role="context",
        provider="nebius_token_factory",
    )

    await _drain(model)

    assert health.failures, "truncation neither cooled down nor disabled the provider"
    assert not health.disabled
