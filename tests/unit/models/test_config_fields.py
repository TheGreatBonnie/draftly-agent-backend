"""Tests for ModelConfig routing metadata fields."""

from draftly.models.config import ModelConfig


def test_model_config_defaults_are_none():
    config = ModelConfig(name="m", provider="p", model_id="org/m")
    assert config.context_window is None
    assert config.input_cost_per_1m_tokens is None
    assert config.output_cost_per_1m_tokens is None


def test_model_config_accepts_routing_metadata():
    config = ModelConfig(
        name="m", provider="p", model_id="org/m",
        context_window=200000,
        input_cost_per_1m_tokens=3.0,
        output_cost_per_1m_tokens=15.0,
    )
    assert config.context_window == 200000
    assert config.input_cost_per_1m_tokens == 3.0


def test_existing_fields_unaffected():
    config = ModelConfig(
        name="m", provider="p", model_id="org/m",
        capabilities=("reasoning",), priority=10, enabled=True,
    )
    assert config.capabilities == ("reasoning",)
