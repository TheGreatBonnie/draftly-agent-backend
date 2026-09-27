"""Timeout/overload must fail over, and must not be confused with real bugs.

Regression: ``PaymentAwareModel`` only rotated on a 402, so a request that
timed out burned ``max_retries x timeout`` (3x60s) on one provider and then
raised -- the router's cross-provider redundancy never engaged.

Timeout detection is by *exception type*, not by ``_classify_failure``:
that helper defaults every unrecognised error to ``FAILURE_TIMEOUT`` (see
``router.py`` "unrelated errors default to FAILURE_TIMEOUT"), so trusting the
label would make a ``RuntimeError`` in our own code silently rotate providers
and triple its latency. A timeout is a fact about the exception type.
"""

from __future__ import annotations

import asyncio

import httpx
import openai
import pytest
from strands.types.exceptions import EventLoopException

from draftly.integrations.strands.models import PaymentAwareModel

from .test_payment_failover import _FakeDecision, _FakeModel, _FakeRouter, _drain

_REQUEST = httpx.Request("POST", "https://api.tokenfactory.nebius.com/v1/chat/completions")


class _TimeoutModel(_FakeModel):
    """Raises a real openai APITimeoutError -- the shape seen in the failed runs."""

    def __init__(self, name: str, *, wrapped: bool = False) -> None:
        super().__init__(name)
        self.wrapped = wrapped

    def _boom(self) -> Exception:
        err = openai.APITimeoutError(request=_REQUEST)
        return EventLoopException(err) if self.wrapped else err

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        raise self._boom()
        yield  # pragma: no cover - makes this an async generator

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        self.structured_calls += 1
        raise self._boom()
        yield  # pragma: no cover - makes this an async generator


def _router(*decisions):
    router = _FakeRouter(list(decisions) or [_FakeDecision("openrouter-pro", "openrouter")])
    return router


def _wrapper(router, model):
    return PaymentAwareModel(model, router=router, role="context", provider="requesty")


def test_timeout_rotates_to_the_next_model():
    router = _router()
    wrapper = _wrapper(router, _TimeoutModel("requesty-pro"))

    events = asyncio.run(_drain(wrapper.stream("messages")))

    assert [e.get("data") for e in events if e.get("chunk_type") == "message_stop"] == [
        "openrouter-pro"
    ]


def test_timeout_records_failure_instead_of_disabling_the_provider():
    """A stall is usually transient; permanently disabling poisons a long-lived worker."""
    router = _router()
    wrapper = _wrapper(router, _TimeoutModel("requesty-pro"))

    asyncio.run(_drain(wrapper.stream("messages")))

    health = router.health.get("requesty")
    assert health.disabled is False
    assert health.recorded == ["timeout"]


def test_strands_wrapped_timeout_still_fails_over():
    """strands re-raises the openai error inside EventLoopException; unwrap it."""
    router = _router()
    wrapper = _wrapper(router, _TimeoutModel("requesty-pro", wrapped=True))

    events = asyncio.run(_drain(wrapper.stream("messages")))

    assert [e.get("data") for e in events if e.get("chunk_type") == "message_stop"] == [
        "openrouter-pro"
    ]


def test_structured_output_timeout_rotates():
    """The failing path in production: structured_output, not stream."""
    router = _router()
    wrapper = _wrapper(router, _TimeoutModel("requesty-pro"))

    events = asyncio.run(_drain(wrapper.structured_output(str, ["hi"])))

    assert [e["data"]["model"] for e in events] == ["openrouter-pro"]


def test_unrelated_error_does_not_rotate_providers():
    """A bug in our own code must surface, not be retried against another provider."""
    router = _router()

    class _Buggy:
        async def stream(self, *args, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    wrapper = _wrapper(router, _Buggy())

    with pytest.raises(RuntimeError):
        asyncio.run(_drain(wrapper.stream("messages")))

    assert router.requests == []
    assert router.health.get("requesty").disabled is False


def test_rotation_is_bounded_by_the_routers_own_candidates():
    """Failover re-routes through route(), so enabled_providers still governs it."""
    router = _router()
    wrapper = _wrapper(router, _TimeoutModel("requesty-pro"))

    asyncio.run(_drain(wrapper.stream("messages")))

    assert len(router.requests) == 1


def test_failover_budget_survives_sdk_retries_being_removed():
    """max_retries is now 0, so the wrapper alone must preserve 3 total attempts."""
    assert PaymentAwareModel._MAX_FAILOVERS == 2
