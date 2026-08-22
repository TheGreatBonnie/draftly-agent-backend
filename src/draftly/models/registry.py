from .config import EmbeddingConfig, ModelConfig
from .providers.base import ModelProvider


class ModelRegistry:
    """
    Central registry of Draftly models and providers.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._models: dict[str, ModelConfig] = {}
        self._embedding_models: dict[str, EmbeddingConfig] = {}

    def register_provider(
        self,
        provider: ModelProvider,
    ) -> None:
        self._providers[provider.name] = provider

    def register_model(
        self,
        model: ModelConfig,
    ) -> None:
        if model.provider not in self._providers:
            raise ValueError(f"Provider '{model.provider}' must be registered before its models.")

        self._models[model.name] = model

    def register_embedding_model(
        self,
        model: EmbeddingConfig,
    ) -> None:
        if model.provider not in self._providers:
            raise ValueError(
                f"Provider '{model.provider}' must be registered before its embedding models."
            )

        self._embedding_models[model.name] = model

    def list_embedding_models(
        self,
    ) -> list[EmbeddingConfig]:
        return [model for model in self._embedding_models.values() if model.enabled]

    def get_model(
        self,
        name: str,
    ) -> ModelConfig:

        if name not in self._models:
            raise KeyError(f"Unknown Draftly model: {name}")

        return self._models[name]

    def get_provider(
        self,
        name: str,
    ) -> ModelProvider:

        if name not in self._providers:
            raise KeyError(f"Unknown model provider: {name}")

        return self._providers[name]

    def list_models(
        self,
        capability: str | None = None,
    ) -> list[ModelConfig]:

        models = [model for model in self._models.values() if model.enabled]

        if capability:
            models = [model for model in models if capability in model.capabilities]

        return sorted(
            models,
            key=lambda model: model.priority,
        )

    def providers(self) -> list[str]:
        return list(self._providers.keys())
