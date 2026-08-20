
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from ..config import EmbeddingConfig, ModelConfig
from .base import ModelProvider


class OrcaRouterProvider(ModelProvider):

    @property
    def name(self) -> str:
        return "orcarouter"

    def create_model(
        self,
        config: ModelConfig,
    ) -> ChatOpenAI:

        if not self.config.api_key:
            raise ValueError(
                "ORCAROUTER_API_KEY is not configured."
            )

        return ChatOpenAI(
            model=config.model_id,
            api_key=SecretStr(self.config.api_key) if self.config.api_key else None,
            base_url=self.config.base_url,
            temperature=config.temperature,
            max_completion_tokens=config.max_tokens,
            timeout=self.config.timeout,
            max_retries=self.config.max_retries,
        )

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> OpenAIEmbeddings:

        if not self.config.api_key:
            raise ValueError(
                "ORCAROUTER_API_KEY is not configured."
            )

        return OpenAIEmbeddings(
            model=config.model_id,
            api_key=SecretStr(self.config.api_key) if self.config.api_key else None,
            base_url=self.config.base_url,
        )
