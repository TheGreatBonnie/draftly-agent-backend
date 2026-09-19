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


class NebiusTokenFactoryModel(OpenAIModel):
    """``OpenAIModel`` that omits an empty ``tools`` array.

    Token Factory's vLLM-backed endpoints reject ``tools: []`` with HTTP
    400 ("`tools` must not be an empty array"). Strands' base class always
    includes the key, so drop it when no tools are supplied. This affects
    tool-less chat calls and ``structured_output`` requests.
    """

    def format_request(
        self,
        messages: Any,
        tool_specs: Any = None,
        system_prompt: Any = None,
        tool_choice: Any = None,
        *,
        system_prompt_content: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        request = super().format_request(
            messages,
            tool_specs=tool_specs,
            system_prompt=system_prompt,
            tool_choice=tool_choice,
            system_prompt_content=system_prompt_content,
            **kwargs,
        )
        if not request.get("tools"):
            request.pop("tools", None)
            request.pop("tool_choice", None)
        return request


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

        return NebiusTokenFactoryModel(
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
