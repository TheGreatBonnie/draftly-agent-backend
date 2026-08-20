from langchain_openai import OpenAIEmbeddings
from langchain_openrouter import ChatOpenRouter  # ty: ignore[unresolved-import]
from pydantic import SecretStr

from ..config import EmbeddingConfig, ModelConfig
from .base import ModelProvider


class OpenRouterProvider(ModelProvider):

    @property
    def name(self) -> str:
        return "openrouter"

    def create_model(
        self,
        config: ModelConfig,
    ) -> ChatOpenRouter:

        if not self.config.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not configured."
            )

        return ChatOpenRouter(
            model=config.model_id,
            api_key=SecretStr(self.config.api_key),
            base_url=self.config.base_url
            or "https://openrouter.ai/api/v1",
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout=int(self.config.timeout * 1000),
            max_retries=self.config.max_retries,
        )

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> OpenAIEmbeddings:

        if not self.config.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not configured."
            )

        return OpenAIEmbeddings(
            model=config.model_id,
            api_key=SecretStr(self.config.api_key) if self.config.api_key else None,
            base_url=self.config.base_url or "https://openrouter.ai/api/v1",
        )
