import structlog
from strands.models.model import Model

from .config import ModelConfig
from .health import (
    FAILURE_AUTH,
    FAILURE_CONTEXT_LENGTH,  # noqa: F401 - reserved for future classification
    FAILURE_INVALID_REQUEST,
    FAILURE_PAYMENT,
    FAILURE_RATE_LIMIT,
    FAILURE_TIMEOUT,
    FAILURE_UNAVAILABLE,
    ProviderHealthRegistry,
)
from .performance import EMAStatsStore, ModelHealthRegistry
from .policies import FALLBACKS, KNOWN_PROVIDERS, RoutingPolicy
from .registry import ModelRegistry
from .schemas import RoutingDecision, RoutingRequest, TaskType

logger = structlog.get_logger(__name__)

#: Capabilities each task type minimally requires (reference §34/§5).
TASK_TYPE_CAPABILITIES: dict[TaskType, frozenset[str]] = {
    TaskType.DOCUMENTATION_GENERATION: frozenset(
        {"reasoning", "tool_calling", "structured_output"}
    ),
    TaskType.DOCUMENTATION_REVIEW: frozenset({"verification"}),
    TaskType.EVALUATION: frozenset({"evaluation"}),
    # SUPPORT/FAST/REASONING/RESEARCH: no hard capability floor beyond text
}


class NoCandidateError(RuntimeError):
    """Raised when the constraint pipeline eliminates every candidate."""


class ModelRouter:
    """
    Dynamically selects and instantiates Draftly models.

    Agents interact with this class rather than providers directly.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        health: ProviderHealthRegistry,
        *,
        enabled_providers: set[str] | None = None,
        stats_store: EMAStatsStore | None = None,
        model_health: ModelHealthRegistry | None = None,
    ) -> None:
        # Existing public attribute names preserved -- resolve()/factory
        # call sites keep working untouched.
        self.registry = registry
        self.health = health
        self._enabled_providers = (
            set(enabled_providers)
            if enabled_providers is not None
            else set(KNOWN_PROVIDERS)
        )
        self._stats_store = stats_store or EMAStatsStore()
        self._model_health = model_health or ModelHealthRegistry()

    @property
    def stats_store(self) -> EMAStatsStore:
        """Live stats cache (composition warm-starts this from PostgreSQL)."""
        return self._stats_store

    @property
    def model_health(self) -> ModelHealthRegistry:
        return self._model_health

    def route(
        self,
        request: RoutingRequest,
        *,
        required_caps: set[str] | None = None,
    ) -> RoutingDecision:
        """Adaptive routing API: constraints -> score -> decide.

        Additive to the legacy API; does NOT replace resolve().
        """
        from .constraints import ConstraintPipeline
        from .profiles import get_profile
        from .scoring import score_candidates

        profile = get_profile(request.task_type)
        caps = required_caps
        if caps is None:
            caps = set(TASK_TYPE_CAPABILITIES.get(request.task_type, frozenset()))

        pipeline = ConstraintPipeline()
        filtered = pipeline.filter_candidates(
            request,
            self.registry.list_models(),
            provider_health=self.health,
            model_health=self._model_health,
            enabled_providers=self._enabled_providers,
            required_caps=caps or None,
            stats_store=self._stats_store,
        )

        if not filtered:
            raise NoCandidateError(
                f"No models available for {request.task_type.value}: "
                f"rejected={pipeline.rejected}"
            )

        scored = score_candidates(request, filtered, profile, self._stats_store)
        best_model, best_score = scored[0]

        reason_codes = ["profile_" + profile.name]
        if caps and caps.issubset(set(best_model.capabilities)):
            reason_codes.append("required_capabilities_match")
        if self.health.get(best_model.provider).available():
            reason_codes.append("healthy_provider")
        if best_model.input_cost_per_1m_tokens is not None:
            reason_codes.append("priced_model")
        if self._stats_store.has_enough_samples(request.task_type.value, best_model.name):
            reason_codes.append("known_task_performance")
        else:
            reason_codes.append("conservative_defaults")

        return RoutingDecision(
            selected_model=best_model.name,
            provider=best_model.provider,
            score=best_score,
            candidates_considered=len(scored),
            profile=profile.name,
            task_type=request.task_type.value,
            estimated_latency_ms=self._stats_store.get_latency_p95(
                request.task_type.value, best_model.name
            ),
            rejected=pipeline.rejected,
            ranked=tuple((m.name, s) for m, s in scored),
            reason_codes=tuple(reason_codes),
            fallback_chain=tuple(m.name for m, _ in scored[1:4]),
        )

    def resolve(
        self,
        policy: RoutingPolicy,
    ) -> Model:
        """
        Select and instantiate the best healthy model for ``policy``.

        Failure handling (doc §19):

            - ``invalid_request``  -> re-raise immediately (fail fast).
            - ``authentication``   -> disable the provider, try next.
            - otherwise            -> record the failure type and, when
                                      ``allow_fallback``, try the next
                                      candidate; bounded by max_attempts.
        """

        candidates = self.registry.list_models()

        candidates = [
            model
            for model in candidates
            if self._supports_capabilities(
                model,
                policy,
            )
        ]

        ordered = self._order_candidates(
            candidates,
            policy,
        )

        errors: list[Exception] = []

        attempts = 0

        for config in ordered:
            if attempts >= policy.max_attempts:
                break

            provider_health = self.health.get(config.provider)

            if not provider_health.available():
                continue

            provider = self.registry.get_provider(config.provider)

            if not provider.is_enabled():
                continue

            attempts += 1

            logger.info(
                "router attempting provider=%s model=%s attempt=%d",
                config.provider,
                config.name,
                attempts,
            )

            try:
                model = provider.create_model(config)

                provider_health.record_success()

                logger.info(
                    "router resolved provider=%s model=%s",
                    config.provider,
                    config.name,
                )

                return model

            except Exception as exc:
                failure = self._classify_failure(exc)

                logger.warning(
                    "router failure provider=%s model=%s type=%s error=%s",
                    config.provider,
                    config.name,
                    failure,
                    exc,
                )

                if failure == FAILURE_PAYMENT:
                    # Billing exhaustion (e.g. router 402) is a hard
                    # provider-level outage: disable so the next resolve()
                    # skips the provider until explicitly reset.
                    provider_health.disable()
                    errors.append(exc)

                    if not policy.allow_fallback:
                        raise

                    continue

                if failure == FAILURE_INVALID_REQUEST:
                    raise

                if failure == FAILURE_AUTH:
                    provider_health.disable()
                    errors.append(exc)

                    if not policy.allow_fallback:
                        raise

                    continue

                provider_health.record_failure(failure)

                errors.append(exc)

                if not policy.allow_fallback:
                    raise

        raise RuntimeError("No healthy Draftly model was available.") from (
            errors[-1] if errors else None
        )

    def resolve_model(
        self,
        model_name: str,
    ) -> Model:

        config = self.registry.get_model(model_name)

        provider_health = self.health.get(config.provider)

        if not provider_health.available():
            raise RuntimeError(f"Provider '{config.provider}' is currently unavailable.")

        provider = self.registry.get_provider(config.provider)

        return provider.create_model(config)

    def resolve_capability(
        self,
        capability: str,
    ) -> Model:
        """
        Resolve the highest-priority healthy model that supports
        the requested capability.

        Unknown capability names raise ``ValueError`` (fail fast on
        typos); exhaustion of all healthy providers raises
        ``RuntimeError``. Every resolution is logged.
        """

        from .capabilities import CapabilityMatcher

        CapabilityMatcher.validate_capability(capability)

        logger.info(
            "resolving capability=%s allow_fallback=%s",
            capability,
            True,
        )

        try:
            return self.resolve(
                RoutingPolicy(
                    required_capabilities=(capability,),
                    allow_fallback=True,
                )
            )

        except RuntimeError as exc:
            logger.error(
                "no healthy model for capability=%s: %s",
                capability,
                exc,
            )
            raise

    @staticmethod
    def _supports_capabilities(
        model: ModelConfig,
        policy: RoutingPolicy,
    ) -> bool:

        return all(capability in model.capabilities for capability in policy.required_capabilities)

    @staticmethod
    def _order_candidates(
        candidates: list[ModelConfig],
        policy: RoutingPolicy,
    ) -> list[ModelConfig]:
        """
        Order candidates by (preferred model, fallback chain, priority).

        The chain is ``policy.fallback_chain`` when set, otherwise the
        per-capability chain for the first required capability.
        """

        chain = policy.fallback_chain or FALLBACKS.get(
            policy.required_capabilities[0] if policy.required_capabilities else "",
            (),
        )

        preferred = {name: index for index, name in enumerate(policy.preferred_models)}

        chain_index = {provider: index for index, provider in enumerate(chain)}

        return sorted(
            candidates,
            key=lambda model: (
                preferred.get(
                    model.name,
                    9999,
                ),
                chain_index.get(
                    model.provider,
                    9999,
                ),
                model.priority,
            ),
        )

    @staticmethod
    def _classify_failure(
        exc: Exception,
    ) -> str:
        """
        Map an exception to a failure type constant.

        Matching uses the exception type name and message text; message
        matching is intentionally conservative (specific status codes
        first) so unrelated errors default to ``FAILURE_TIMEOUT``.
        """

        name = type(exc).__name__.lower()
        message = str(exc).lower()

        if "auth" in name or "401" in message or "api key" in message or "unauthorized" in message:
            return FAILURE_AUTH

        if (
            "402" in message
            or "payment" in message
            or "billing" in message
            or "insufficient balance" in message
            or "balance is too low" in message
        ):
            return FAILURE_PAYMENT

        if "429" in message or "rate" in message or "limit" in message:
            return FAILURE_RATE_LIMIT

        if "503" in message or "unavailable" in message or "overloaded" in message:
            return FAILURE_UNAVAILABLE

        if "400" in message or "invalid" in message or "bad request" in message:
            return FAILURE_INVALID_REQUEST

        return FAILURE_TIMEOUT
