from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str | None
    base_url: str | None
    enabled: bool = True
    priority: int = 100
    timeout: float = 60.0
    max_retries: int = 2


@dataclass(frozen=True)
class ModelConfig:
    name: str
    provider: str
    model_id: str

    capabilities: tuple[str, ...] = ()

    temperature: float = 0.0
    max_tokens: int | None = None

    priority: int = 100

    enabled: bool = True


@dataclass(frozen=True)
class EmbeddingConfig:
    """Configuration for a single embedding model served by one provider."""

    name: str
    provider: str
    model_id: str
    dimensions: int
    priority: int = 100
    enabled: bool = True
