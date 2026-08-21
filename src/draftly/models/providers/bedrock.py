"""Amazon Bedrock provider (native Strands integration)."""

from __future__ import annotations

from typing import Any

from strands.models import BedrockModel
from strands.models.model import Model

from ..config import EmbeddingConfig, ModelConfig
from .base import ModelProvider


class BedrockProvider(ModelProvider):
    """Provider for Amazon Bedrock foundation models."""

    @property
    def name(self) -> str:
        return "bedrock"

    def create_model(
        self,
        config: ModelConfig,
    ) -> Model:
        # Bedrock uses AWS credentials from environment/IAM role
        # base_url in ProviderConfig is used as region_name
        region = self.config.base_url or "us-east-1"

        return BedrockModel(
            model_id=config.model_id,
            region_name=region,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            streaming=True,
        )

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> Any:
        # Bedrock doesn't have a unified embeddings endpoint like OpenAI
        # Use dedicated embedding provider (Titan, Cohere) via OpenAI-compatible gateway
        # or a separate provider implementation
        raise NotImplementedError(
            "Bedrock embeddings not supported directly. "
            "Use Titan/Cohere via OpenAI-compatible gateway or dedicated embedding provider."
        )
