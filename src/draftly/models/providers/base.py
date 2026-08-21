"""Provider abstraction used by Draftly's model registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from strands.models.model import Model

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
    ) -> Model:
        """
        Create a Strands-compatible chat model.
        """

    def create_embedder(
        self,
        config: EmbeddingConfig,
    ) -> Any:
        """Create an OpenAI-compatible embedding client."""
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
