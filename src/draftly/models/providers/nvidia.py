"""NVIDIA NIM provider (OpenAI-compatible endpoints)."""

from __future__ import annotations

from typing import Any

from strands.models import OpenAIModel
from strands.models.model import Model

from ..config import EmbeddingConfig, ModelConfig
from .base import ModelProvider


class NvidiaProvider(ModelProvider):
    """Provider for NVIDIA NIM / integrated.api.nvidia.com."""

    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

    @property
    def name(self) -> str:
        return "nvidia"

    def create_model(
        self,
        config: ModelConfig,
    ) -> Model:
        if not self.config.api_key:
            raise ValueError("NVIDIA_API_KEY is not configured.")

        return OpenAIModel(
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

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> Any:
        from ..embeddings import OpenAICompatibleEmbedder

        if not self.config.api_key:
            raise ValueError("NVIDIA_API_KEY is not configured.")

        return OpenAICompatibleEmbedder(
            api_key=self.config.api_key,
            base_url=self.config.base_url or self.DEFAULT_BASE_URL,
            model_id=config.model_id,
            timeout=self.config.timeout,
        )
