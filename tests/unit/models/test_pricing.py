"""Tests for cost estimation."""

from draftly.models.config import ModelConfig
from draftly.models.pricing import UNPRICED_MODEL_COST, estimate_cost


def test_estimate_cost_with_pricing():
    model = ModelConfig(
        name="test", provider="openrouter", model_id="org/test",
        input_cost_per_1m_tokens=0.50,
        output_cost_per_1m_tokens=1.50,
    )
    cost = estimate_cost(model, input_tokens=1_000_000, output_tokens=500_000)
    assert cost is not None
    expected = 0.50 * 1.0 + 1.50 * 0.5
    assert abs(cost - expected) < 1e-9


def test_unpriced_model_gets_conservative_sentinel():
    model = ModelConfig(name="test", provider="openrouter", model_id="org/test")
    cost = estimate_cost(model, input_tokens=1000, output_tokens=500)
    assert cost == UNPRICED_MODEL_COST


def test_partially_priced_model_uses_known_rates():
    model = ModelConfig(
        name="test", provider="openrouter", model_id="org/test",
        input_cost_per_1m_tokens=0.50,
    )
    # Missing output rate treated as 0 rather than triggering sentinel
    cost = estimate_cost(model, input_tokens=1_000_000, output_tokens=1)
    assert cost is not None
    assert abs(cost - 0.50) < 1e-9
