"""Nebius Token Factory provider (OpenAI-compatible endpoints).

Token Factory exposes ``/v1`` OpenAI-compatible chat and embeddings
endpoints serving NVIDIA Nemotron models and the Qwen embedding family.
See https://docs.tokenfactory.nebius.com/quickstart.
"""

from __future__ import annotations

from typing import Any

from strands.models import OpenAIModel
from strands.models.model import Model

from ..config import EmbeddingConfig, ModelConfig
from .base import ModelProvider

TF_DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"


class NebiusTokenFactoryProvider(ModelProvider):
    """Provider for Nebius Token Factory."""

    @property
    def name(self) -> str:
        return "nebius_token_factory"

    def create_model(
        self,
        config: ModelConfig,
    ) -> Model:
        if not self.config.api_key:
            raise ValueError("NEBIUS_TOKEN_FACTORY_API_KEY is not configured.")

        return OpenAIModel(
            model_id=config.model_id,
            client_args={
                "api_key": self.config.api_key,
                "base_url": self.config.base_url or TF_DEFAULT_BASE_URL,
                "timeout": self.config.timeout,
                "max_retries": self.config.max_retries,
            },
            params={
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            },
        )

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> Any:
        from ..embeddings import OpenAICompatibleEmbedder

        if not self.config.api_key:
            raise ValueError("NEBIUS_TOKEN_FACTORY_API_KEY is not configured.")

        return OpenAICompatibleEmbedder(
            api_key=self.config.api_key,
            base_url=self.config.base_url or TF_DEFAULT_BASE_URL,
            model_id=config.model_id,
            timeout=self.config.timeout,
            dimensions=config.dimensions,
        )
