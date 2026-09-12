"""Payment-402 failover on the per-role Strands model wrapper.

Regression: graph nodes (context/research/intelligence) fail hard on a
requesty 402 ("balance is too low") because the provider is bound once at
build time; the router is never re-consulted at completion-call time.
``PaymentAwareModel`` disables the provider on 402 and re-resolves the
role before retrying once.
"""

import asyncio

import pytest

from draftly.integrations.strands.models import PaymentAwareModel


class _PaymentError(Exception):
    """Shape of the openai/strands 402 seen in the failed runs."""


class _FakeModel:
    def __init__(self, name, *, fail_stream=0, fail_structured=0, fail_tokens=0):
        self.name = name
        self.fail_stream = fail_stream
        self.fail_structured = fail_structured
        self.fail_tokens = fail_tokens
        self.stream_calls = 0
        self.structured_calls = 0
        self.token_calls = 0

    @property
    def stateful(self):
        return False

    @property
    def context_window_limit(self):
        return 128000

    def get_config(self):
        return {"model": self.name}

    def update_config(self, **kwargs):
        self._updated = kwargs

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        if self.fail_stream > 0:
            self.fail_stream -= 1
            raise _PaymentError("Error code: 402 - balance is too low")
        yield {"chunk_type": "message_start"}
        yield {"chunk_type": "message_stop", "data": self.name}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        self.structured_calls += 1
        if self.fail_structured > 0:
            self.fail_structured -= 1
            raise _PaymentError("Your organization's balance is too low")
        yield {"chunk_type": "parsed", "data": {"model": self.name}}

    async def count_tokens(
        self, messages, tool_specs=None, system_prompt=None, system_prompt_content=None
    ):
        self.token_calls += 1
        if self.fail_tokens > 0:
            self.fail_tokens -= 1
            raise _PaymentError("Insufficient balance")
        return 100


class _FakeModelConfig:
    def __init__(self, name, provider):
        self.name = name
        self.provider = provider


class _FakeProvider:
    def __init__(self, name):
        self.name = name

    def create_model(self, config):
        return _FakeModel(config.name)


class _FakeDecision:
    def __init__(self, selected_model, provider):
        self.selected_model = selected_model
        self.provider = provider


class _FakeRegistry:
    def __init__(self):
        self._models = {
            "requesty-pro": _FakeModelConfig("requesty-pro", "requesty"),
            "openrouter-pro": _FakeModelConfig("openrouter-pro", "openrouter"),
        }
        self._providers = {
            "requesty": _FakeProvider("requesty"),
            "openrouter": _FakeProvider("openrouter"),
        }

    def get_model(self, name):
        return self._models[name]

    def get_provider(self, provider):
        return self._providers[provider]


class _FakeHealthEntry:
    def __init__(self):
        self.disabled = False
        self.disable_count = 0

    def disable(self):
        self.disabled = True
        self.disable_count += 1

    def available(self):
        return not self.disabled


class _FakeHealth:
    def __init__(self):
        self._entries = {}

    def get(self, provider):
        return self._entries.setdefault(provider, _FakeHealthEntry())


class _FakeRouter:
    """Duck-type ModelRouter: route() + health + registry."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.health = _FakeHealth()
        self.registry = _FakeRegistry()
        self.requests = []

    def route(self, request):
        self.requests.append(request)
        return self.decisions.pop(0)


def _router():
    return _FakeRouter(
        [
            _FakeDecision("openrouter-pro", "openrouter"),
        ]
    )


async def _drain(agen):
    return [chunk async for chunk in agen]


def test_stream_402_failover_disables_provider_and_retries():
    router = _router()
    inner = _FakeModel("requesty-pro", fail_stream=1)
    wrapper = PaymentAwareModel(inner, router=router, role="research", provider="requesty")

    events = asyncio.run(_drain(wrapper.stream("messages")))

    finals = [e.get("data") for e in events if e.get("chunk_type") == "message_stop"]
    assert finals == ["openrouter-pro"]
    assert router.health.get("requesty").disabled is True
    assert router.health.get("openrouter").disabled is False
    assert inner.stream_calls == 1


def test_stream_non_payment_error_propagates_without_failover():
    router = _router()

    class _Generic:
        async def stream(self, *args, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover - makes this an async generator

    wrapper = PaymentAwareModel(_Generic(), router=router, role="research", provider="requesty")

    with pytest.raises(RuntimeError):
        asyncio.run(_drain(wrapper.stream("messages")))

    assert router.health.get("requesty").disabled is False
    assert router.requests == []


def test_structured_output_402_failover_retries():
    router = _router()
    inner = _FakeModel("requesty-pro", fail_structured=1)
    wrapper = PaymentAwareModel(inner, router=router, role="context", provider="requesty")

    events = asyncio.run(_drain(wrapper.structured_output(str, ["hi"])))

    assert [e["data"]["model"] for e in events] == ["openrouter-pro"]
    assert router.health.get("requesty").disabled is True


def test_count_tokens_402_failover_retries():
    router = _router()
    inner = _FakeModel("requesty-pro", fail_tokens=1)
    wrapper = PaymentAwareModel(inner, router=router, role="research", provider="requesty")

    count = asyncio.run(wrapper.count_tokens(["hi"]))

    assert count == 100
    assert router.health.get("requesty").disabled is True


def test_failover_reroutes_via_same_role():
    router = _router()
    wrapper = PaymentAwareModel(
        _FakeModel("requesty-pro", fail_stream=1),
        router=router,
        role="github_intelligence",
        provider="requesty",
    )

    asyncio.run(_drain(wrapper.stream("messages")))

    assert [getattr(r, "task_type", None) for r in router.requests] == ["research"]
