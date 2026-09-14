"""Amazon Bedrock Mantle providers (OpenAI-compatible endpoint)."""

from __future__ import annotations

from typing import Any

from strands.models import OpenAIModel
from strands.models.model import Model

from ..config import EmbeddingConfig, ModelConfig
from .base import ModelProvider


class MantleProvider(ModelProvider):
    """Provider for Amazon Bedrock Mantle's in-region native route.

    Reaches the native model catalog served at ``.../v1`` (the idempotency
    endpoint documented for the region). The OpenAI-translated frontend
    (grok/gemma) is reached through ``MantleOpenAIProvider`` instead.
    """

    DEFAULT_BASE_URL = "https://bedrock-mantle.us-east-1.api.aws/v1"

    @property
    def name(self) -> str:
        return "mantle"

    def create_model(
        self,
        config: ModelConfig,
    ) -> Model:
        if not self.config.api_key:
            raise ValueError("MANTLE_API_KEY is not configured.")

        model: Model = OpenAIModel(
            model_id=config.model_id,
            client_args={
                "api_key": self.config.api_key,
                "base_url": self.config.base_url or self.DEFAULT_BASE_URL,
                "timeout": self.config.timeout,
                "max_retries": self.config.max_retries,
            },
            params={
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            },
        )
        return model

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> Any:
        # Mantle doesn't have a standard embeddings endpoint
        # Use existing OpenAI-compatible embedding providers (OpenRouter, Requesty, etc.)
        raise NotImplementedError(
            "Mantle embeddings not supported directly. "
            "Use OpenAI-compatible embedding providers (OpenRouter, Requesty, etc.)."
        )


class MantleOpenAIProvider(MantleProvider):
    """Provider for the OpenAI-translated Mantle frontend route.

    The Bedrock Mantle gateway exposes disjoint model catalogs on two of
    its routes: the native in-region route (``.../v1``) serves the eight
    "native" model IDs while the OpenAI-translated frontend
    (``.../openai/v1``) only serves ``xai.grok-4.3`` and
    ``google.gemma-4-31b``. This provider pins the translated frontend so
    those models reach the route that accepts them.
    """

    DEFAULT_BASE_URL = "https://bedrock-mantle.us-east-1.api.aws/openai/v1"

    @property
    def name(self) -> str:
        return "mantle-openai"
