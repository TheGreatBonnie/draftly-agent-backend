from collections.abc import Sequence

import structlog
from openai import OpenAI

from .config import EmbeddingConfig
from .health import (
    FAILURE_AUTH,
    FAILURE_INVALID_REQUEST,
    ProviderHealthRegistry,
)
from .registry import ModelRegistry

__all__ = ["EmbeddingRouter", "OpenAICompatibleEmbedder"]

logger = structlog.get_logger(__name__)


class OpenAICompatibleEmbedder:
    """
    Minimal OpenAI-compatible embedding client.

    Strands provides no embedding API, so Draftly talks to the
    provider's ``/embeddings`` endpoint directly via the ``openai``
    client. ``embed_query`` mirrors the LangChain interface so the
    router's call sites stay stable.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None,
        model_id: str,
        timeout: float = 60.0,
    ) -> None:
        self.model_id = model_id
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        response = self._client.embeddings.create(
            model=self.model_id,
            input=text,
        )

        return response.data[0].embedding

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        response = self._client.embeddings.create(
            model=self.model_id,
            input=list(texts),
        )
        return [item.embedding for item in response.data]


class EmbeddingRouter:
    """
    Dynamically selects a healthy provider for embedding generation.

    All candidates serve the same ``model_id`` (same vector space);
    the router picks the highest-priority healthy one and falls back
    across providers using the same failure classification as
    ``ModelRouter``. Dimension mismatches fail fast.

    Invariants:
        - Same-model invariant: every candidate serves the same
          ``model_id`` (``EMBEDDING_MODEL_ID``), so the vector space
          is identical across providers.
        - Dimension invariant: ``memories.embedding`` is
          ``VECTOR(1536)``; the router validates returned vector length
          and fails fast on mismatch.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        health: ProviderHealthRegistry,
    ) -> None:
        self.registry = registry
        self.health = health
        self.last_provider: str | None = None

    def embed(
        self,
        text: str,
    ) -> Sequence[float]:
        candidates = self._ordered_candidates()

        errors: list[Exception] = []

        for config in candidates:
            provider_health = self.health.get(config.provider)

            if not provider_health.available():
                continue

            provider = self.registry.get_provider(config.provider)

            if not provider.is_enabled():
                continue

            logger.info(
                "embedding router attempting provider=%s model=%s",
                config.provider,
                config.model_id,
            )

            try:
                embedder = provider.create_embedder(config)

                vector = embedder.embed_query(text)

            except Exception as exc:
                from .router import ModelRouter

                failure = ModelRouter._classify_failure(exc)

                logger.warning(
                    "embedding router failure provider=%s model=%s type=%s error=%s",
                    config.provider,
                    config.model_id,
                    failure,
                    exc,
                )

                if failure == FAILURE_INVALID_REQUEST:
                    raise

                if failure == FAILURE_AUTH:
                    provider_health.disable()
                    errors.append(exc)
                    continue

                provider_health.record_failure(failure)
                errors.append(exc)
                continue

            self._validate_dimensions(config, vector)

            provider_health.record_success()

            self.last_provider = config.provider

            logger.info(
                "embedding router resolved provider=%s model=%s dims=%d",
                config.provider,
                config.model_id,
                len(vector),
            )

            return vector

        raise RuntimeError("No healthy embedding provider was available.") from (
            errors[-1] if errors else None
        )

    def embed_batch(self, texts: Sequence[str]) -> list[Sequence[float]]:
        candidates = self._ordered_candidates()
        errors: list[Exception] = []

        for config in candidates:
            provider_health = self.health.get(config.provider)
            if not provider_health.available():
                continue
            provider = self.registry.get_provider(config.provider)
            if not provider.is_enabled():
                continue

            logger.info(
                "embedding batch attempting provider=%s model=%s count=%d",
                config.provider, config.model_id, len(texts),
            )
            try:
                embedder = provider.create_embedder(config)
                vectors = embedder.embed_queries(texts)
            except Exception as exc:
                from .router import ModelRouter

                failure = ModelRouter._classify_failure(exc)
                logger.warning(
                    "embedding batch failure provider=%s model=%s type=%s error=%s",
                    config.provider, config.model_id, failure, exc,
                )
                if failure == FAILURE_INVALID_REQUEST:
                    raise
                if failure == FAILURE_AUTH:
                    provider_health.disable()
                    errors.append(exc)
                    continue
                provider_health.record_failure(failure)
                errors.append(exc)
                continue

            for vector in vectors:
                self._validate_dimensions(config, vector)
            provider_health.record_success()
            self.last_provider = config.provider
            logger.info(
                "embedding batch resolved provider=%s model=%s count=%d",
                config.provider, config.model_id, len(vectors),
            )
            return vectors

        raise RuntimeError("No healthy embedding provider was available.") from (
            errors[-1] if errors else None
        )

    def _ordered_candidates(
        self,
    ) -> list[EmbeddingConfig]:
        candidates = self.registry.list_embedding_models()

        candidates.sort(key=lambda config: config.priority)

        if not candidates:
            return []

        primary_model_id = candidates[0].model_id

        return [config for config in candidates if config.model_id == primary_model_id]

    @staticmethod
    def _validate_dimensions(
        config: EmbeddingConfig,
        vector: Sequence[float],
    ) -> None:
        if len(vector) != config.dimensions:
            raise RuntimeError(
                f"Embedding dimension mismatch for model "
                f"'{config.model_id}' via provider '{config.provider}': "
                f"expected {config.dimensions} dims, got {len(vector)}. "
                "Check the embedding model and VECTOR() schema."
            )
