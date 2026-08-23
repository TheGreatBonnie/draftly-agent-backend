"""Tests for ModelRegistry."""

from typing import cast

import pytest

from draftly.models.config import ModelConfig, ProviderConfig
from draftly.models.providers.base import ModelProvider
from draftly.models.registry import ModelRegistry


@pytest.fixture()
def registry():
    reg = ModelRegistry()
    reg.register_provider(
        cast(ModelProvider, ProviderConfig(name="test-provider", api_key="k", base_url=None))
    )
    return reg


def test_register_model_raises_on_duplicate_name(registry):
    config = ModelConfig(name="dup", provider="test-provider", model_id="m1")
    registry.register_model(config)

    with pytest.raises(ValueError, match="Duplicate model name"):
        registry.register_model(
            ModelConfig(name="dup", provider="test-provider", model_id="m2")
        )


def test_register_model_allows_different_names(registry):
    registry.register_model(
        ModelConfig(name="a", provider="test-provider", model_id="m1")
    )
    registry.register_model(
        ModelConfig(name="b", provider="test-provider", model_id="m2")
    )
    assert len(registry.list_models()) == 2
