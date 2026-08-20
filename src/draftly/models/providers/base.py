from abc import ABC, abstractmethod
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from ..config import EmbeddingConfig, ModelConfig, ProviderConfig


class ModelProvider(ABC):
    """
    Provider abstraction used by Draftly's model registry.
    """

    def __init__(
        self,
        provider_config: ProviderConfig,
    ) -> None:
        self.config = provider_config

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier."""

    @abstractmethod
    def create_model(
        self,
        config: ModelConfig,
    ) -> BaseChatModel:
        """
        Create a LangChain-compatible chat model.
        """

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> Embeddings:
        """Create a LangChain-compatible embedding model."""
        raise NotImplementedError(
            f"Provider '{self.name}' does not support embeddings."
        )

    def is_enabled(self) -> bool:
        return self.config.enabled

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "enabled": self.config.enabled,
            "priority": self.config.priority,
        }
