"""A ``max_tokens`` response must be able to fail over.

Run ``ce8ea540`` (PR #62): ``requesty`` returned 402, failover worked, and
``nemotron-ultra-doc`` then returned a *complete* stream flagged
``stop_reason == "max_tokens"``. Strands raises ``MaxTokensReachedException``
in the event loop at ``event_loop.py:310`` -- after the model generator has
already finished normally -- so ``PaymentAwareModel`` never sees an exception
and never gets to rotate. The node died.

The wrapper only sees the terminating ``stop`` event, so that is where the
signal has to be read.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
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


def _model_ending_with(stop_reason: str) -> Any:
    """A model whose single response terminates with ``stop_reason``."""

    class _M:
        def __init__(self) -> None:
            self.inner = self

        async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
            yield {"stop": (None, {"role": "assistant", "content": []}, stop_reason)}

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
