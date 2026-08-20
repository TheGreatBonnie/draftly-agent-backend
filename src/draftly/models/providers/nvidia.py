from typing import Any

from langchain_nvidia_ai_endpoints import ChatNVIDIA

from ..config import EmbeddingConfig, ModelConfig
from .base import ModelProvider


class NvidiaProvider(ModelProvider):

    @property
    def name(self) -> str:
        return "nvidia"

    def create_model(
        self,
        config: ModelConfig,
    ) -> ChatNVIDIA:

        if not self.config.api_key:
            raise ValueError(
                "NVIDIA_API_KEY is not configured."
            )

        # LangSmith cost tracking: ls_provider and ls_model_name are set via
        # run metadata in the middleware layer, not model kwargs.
        return ChatNVIDIA(
            model=config.model_id,
            nvidia_api_key=self.config.api_key,
            base_url=self.config.base_url
            or "https://integrate.api.nvidia.com/v1",
            temperature=config.temperature,
            max_completion_tokens=config.max_tokens,
            timeout=self.config.timeout,
            model_kwargs={"max_retries": self.config.max_retries},
        )

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> Any:
        if not self.config.api_key:
            raise ValueError(
                "NVIDIA_API_KEY is not configured."
            )

        from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings

        return NVIDIAEmbeddings(
            model=config.model_id,
            api_key=self.config.api_key,
            base_url=self.config.base_url
            or "https://integrate.api.nvidia.com/v1",
        )
