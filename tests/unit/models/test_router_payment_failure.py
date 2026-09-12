"""Payment/billing failure classification and provider disabling.

Regression: a requesty router 402 ("balance is too low") surfaced as a
hard APIStatusError inside the research swarm because ``_classify_failure``
fell through to FAILURE_TIMEOUT, which only records (5-failure threshold to
disable) and does not force the next ``resolve()`` to skip the provider.
"""

from unittest.mock import MagicMock

import pytest

from draftly.models.config import ModelConfig
from draftly.models.health import FAILURE_PAYMENT, ProviderHealthRegistry
from draftly.models.policies import RoutingPolicy
from draftly.models.registry import ModelRegistry
from draftly.models.router import ModelRouter


class APIStatusError(Exception):
    """Shape of the OpenAI/strands client error seen in the run."""

    def __init__(self, message, *, status_code=None):
        self.status_code = status_code
        super().__init__(message)


def _provider(name, fail_with=None):
    provider = MagicMock()
    provider.name = name

    def _create_model(config):
        if fail_with is not None:
            raise fail_with
        return f"MODEL-{name}"

    provider.create_model.side_effect = _create_model
    return provider


def _registry(requesty_fails=True):
    reg = ModelRegistry()
    fail = APIStatusError("Error code: 402 - balance is too low", status_code=402)

    def _create(config):
        if requesty_fails and config.provider == "requesty":
            raise fail
        return f"MODEL-{config.provider}"

    reg.register_provider(_provider("requesty"))
    reg.register_provider(_provider("openrouter"))
    reg.get_provider("requesty").create_model.side_effect = _create
    reg.get_provider("openrouter").create_model.side_effect = _create

    for name, provider in (("rq-pro", "requesty"), ("or-pro", "openrouter")):
        reg.register_model(
            ModelConfig(
                name=name,
                provider=provider,
                model_id=f"org/{name}",
                capabilities=("reasoning", "tool_calling", "structured_output"),
                context_window=128000,
                input_cost_per_1m_tokens=0.5,
                output_cost_per_1m_tokens=1.5,
                priority=1 if name == "rq-pro" else 2,
            )
        )
    return reg


def test_classify_failure_maps_402_balance_to_payment():
    err = APIStatusError(
        "Error code: 402 - {'error': {'origin': 'router', 'message': "
        "\"Your organization's balance is too low to run this request. "
        "Top up at https://app.requesty.ai/settings/billing\"}}",
        status_code=402,
    )

    assert ModelRouter._classify_failure(err) == FAILURE_PAYMENT


def test_classify_failure_maps_payment_keyword_to_payment():
    err = APIStatusError("Insufficient balance for this request")

    assert ModelRouter._classify_failure(err) == FAILURE_PAYMENT


def test_resolve_payment_failure_disables_provider_and_falls_back():
    reg = _registry()

    health = ProviderHealthRegistry()
    router = ModelRouter(reg, health)

    model = router.resolve(
        RoutingPolicy(required_capabilities=("reasoning",), allow_fallback=True)
    )

    assert model == "MODEL-openrouter"
    assert health.get("requesty").disabled is True
    assert health.get("requesty").available() is False


def test_resolve_payment_failure_raises_when_no_fallback_allowed():
    reg = _registry(requesty_fails=True)
    health = ProviderHealthRegistry()
    router = ModelRouter(reg, health)

    with pytest.raises(APIStatusError):
        router.resolve(
            RoutingPolicy(required_capabilities=("reasoning",), allow_fallback=False)
        )

    assert health.get("requesty").disabled is True
