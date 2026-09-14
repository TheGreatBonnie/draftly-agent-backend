import os

import structlog

from .capabilities import CapabilityMatcher
from .config import EmbeddingConfig, ModelConfig, ProviderConfig
from .embeddings import EmbeddingRouter
from .health import ProviderHealthRegistry
from .performance import EMAStatsStore, ModelHealthRegistry
from .policies import (
    FALLBACKS,
    KNOWN_PROVIDERS,
    AgentModelPolicy,
    validate_fallback_chain,
)
from .providers import (
    BedrockProvider,
    MantleOpenAIProvider,
    MantleProvider,
    NvidiaProvider,
    OpenRouterProvider,
    OrcaRouterProvider,
    RequestyProvider,
)
from .registry import ModelRegistry
from .router import ModelRouter

logger = structlog.get_logger(__name__)

#: Runtime provider gate — comma-separated provider names. Unset/empty => all.
ENABLED_PROVIDERS_ENV = "DRAFTLY_ENABLED_PROVIDERS"


def _enabled_providers_from_env() -> set[str] | None:
    """Parse DRAFTLY_ENABLED_PROVIDERS into a provider set, warning on unknowns."""

    raw = os.getenv(ENABLED_PROVIDERS_ENV)

    if not raw:
        return None

    providers = {p.strip().lower() for p in raw.split(",") if p.strip()}

    if not providers:
        return None

    unknown = providers - set(KNOWN_PROVIDERS)

    if unknown:
        logger.warning(
            "enabled_providers_unknown providers=%s ignored",
            sorted(unknown),
        )

    return providers


def _resolve_model_id(
    *env_names: str,
    default: str,
) -> str:
    """
    Resolve a model ID from env vars, skipping unset/empty/blank values.

    Empty configured values are skipped so a truncated ``.env`` cannot
    silently select an empty model ID. Missing values fall back to the
    supplied default and are logged for observability.
    """

    for name in env_names:
        value = os.getenv(name)

        if value and value.strip():
            logger.debug("model env resolved var=%s value=%s", name, value)
            return value.strip()

    logger.debug(
        "model env unresolved vars=%s using default=%s",
        env_names,
        default,
    )

    return default


def build_model_router(
    *,
    stats_store: EMAStatsStore | None = None,
    model_health: ModelHealthRegistry | None = None,
    enabled_providers: set[str] | None = None,
) -> ModelRouter:

    registry = ModelRegistry()

    if enabled_providers is None:
        enabled_providers = _enabled_providers_from_env()

    health = ProviderHealthRegistry()

    # ---------------------------------------------------------
    # Providers
    # ---------------------------------------------------------

    registry.register_provider(
        NvidiaProvider(
            ProviderConfig(
                name="nvidia",
                api_key=os.getenv("NVIDIA_API_KEY"),
                base_url=os.getenv("NVIDIA_BASE_URL"),
                priority=10,
            )
        )
    )

    registry.register_provider(
        RequestyProvider(
            ProviderConfig(
                name="requesty",
                api_key=os.getenv("REQUESTY_API_KEY"),
                base_url=os.getenv("REQUESTY_BASE_URL"),
                priority=20,
            )
        )
    )

    registry.register_provider(
        OrcaRouterProvider(
            ProviderConfig(
                name="orcarouter",
                api_key=os.getenv("ORCAROUTER_API_KEY"),
                base_url=os.getenv("ORCAROUTER_BASE_URL"),
                priority=30,
            )
        )
    )

    registry.register_provider(
        OpenRouterProvider(
            ProviderConfig(
                name="openrouter",
                api_key=os.getenv("OPENROUTER_API_KEY"),
                base_url=os.getenv("OPENROUTER_BASE_URL"),
                priority=40,
            )
        )
    )

    registry.register_provider(
        BedrockProvider(
            ProviderConfig(
                name="bedrock",
                api_key=None,  # Uses IAM role/credentials
                base_url=os.getenv("AWS_REGION", "us-east-1"),
                priority=5,  # High priority for Bedrock models
            )
        )
    )

    registry.register_provider(
        MantleProvider(
            ProviderConfig(
                name="mantle",
                api_key=os.getenv("MANTLE_API_KEY"),
                base_url=os.getenv("MANTLE_ENDPOINT_URL"),
                priority=3,  # Highest priority for Mantle models
            )
        )
    )

    registry.register_provider(
        MantleOpenAIProvider(
            ProviderConfig(
                name="mantle-openai",
                api_key=os.getenv("MANTLE_API_KEY"),
                base_url=os.getenv("MANTLE_OPENAI_ENDPOINT_URL"),
                priority=3,  # Same account, translated frontend (grok/gemma)
            )
        )
    )

    # ---------------------------------------------------------
    # Models
    # ---------------------------------------------------------

    registry.register_model(
        ModelConfig(
            name="reasoning-requesty",
            provider="requesty",
            model_id=_resolve_model_id(
                "REQUESTY_REASONING_MODEL",
                "REQUESTY_DEEPSEEK_REASONING_MODEL",
                default="deepseek/deepseek-reasoner",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
            ),
            priority=20,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-requesty",
            provider="requesty",
            model_id=_resolve_model_id(
                "REQUESTY_FAST_MODEL",
                "REQUESTY_DEEPSEEK_V4_FLASH_MODEL",
                default="tensorx/deepseek-v4-flash",
            ),
            capabilities=("tool_calling",),
            priority=20,

        )
    )

    stage_models = (
        (
            "stage-research",
            "RESEARCH_MODEL",
            "tensorx/deepseek-v4-flash",
            ("research", "tool_calling"),
        ),
        (
            "stage-review",
            "REVIEW_MODEL",
            "tensorx/deepseek-v4-flash",
            ("verification", "tool_calling"),
        ),
        (
            "stage-rubric-grader",
            "RUBRIC_GRADER_MODEL",
            "tensorx/deepseek-v4-flash",
            ("evaluation", "tool_calling"),
        ),
    )

    for stage_name, env_var, default_model, capabilities in stage_models:
        registry.register_model(
            ModelConfig(
                name=stage_name,
                provider="requesty",
                model_id=_resolve_model_id(
                    env_var,
                    default=default_model,
                ),
                capabilities=capabilities,
                priority=50,
            )
        )

    registry.register_model(
        ModelConfig(
            name="research-model",
            provider="requesty",
            model_id=_resolve_model_id(
                "RESEARCH_MODEL",
                "REQUESTY_DEEPSEEK_V4_FLASH_MODEL",
                default="tensorx/deepseek-v4-flash",
            ),
            capabilities=(
                "research",
                "tool_calling",
            ),
            priority=30,

        )
    )

    registry.register_model(
        ModelConfig(
            name="review-model",
            provider="requesty",
            model_id=_resolve_model_id(
                "REVIEW_MODEL",
                "REQUESTY_DEEPSEEK_V4_FLASH_MODEL",
                default="tensorx/deepseek-v4-flash",
            ),
            capabilities=(
                "verification",
                "tool_calling",
            ),
            priority=30,

        )
    )

    registry.register_model(
        ModelConfig(
            name="rubric-grader-model",
            provider="requesty",
            model_id=_resolve_model_id(
                "RUBRIC_GRADER_MODEL",
                "REQUESTY_DEEPSEEK_V4_FLASH_MODEL",
                default="tensorx/deepseek-v4-flash",
            ),
            capabilities=(
                "evaluation",
                "tool_calling",
            ),
            priority=30,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-orca",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "ORCAROUTER_REASONING_MODEL",
                "ORCA_DEEPSEEK_REASONING_MODEL",
                default="deepseek/deepseek-reasoner",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
            ),
            priority=30,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-openrouter",
            provider="openrouter",
            model_id=_resolve_model_id(
                "OPENROUTER_REASONING_MODEL",
                "NEMOTRON_3_ULTRA_MODEL",
                default="openai/gpt-5",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=40,

        )
    )

    # ---------------------------------------------------------
    # .env.example candidate models — registered unconditionally so
    # `make probe-models` can measure them; pruned/ranked by live
    # probe results (see docs/superpowers specs).
    # ---------------------------------------------------------

    registry.register_model(
        ModelConfig(
            name="reasoning-requesty-v4-pro",
            provider="requesty",
            model_id=_resolve_model_id(
                "REQUESTY_DEEPSEEK_V4_PRO_MODEL",
                default="deepseek/deepseek-v4-pro-0813",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
            ),
            priority=20,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-requesty-luna",
            provider="requesty",
            model_id=_resolve_model_id(
                "REQUESTY_OPENAI_5.6_LUNA_MODEL",
                default="openai/gpt-5.6-luna",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=20,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-orca-v4-pro",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "ORCA_DEEPSEEK_V4_PRO_MODEL",
                default="deepseek/deepseek-v4-pro",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=30,

        )
    )

    # structured_output stripped: orca free tier rejects response_format.
    registry.register_model(
        ModelConfig(
            name="fast-orca-v4-pro-free",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "ORCA_DEEPSEEK_V4_PRO_FREE_MODEL",
                default="deepseek/deepseek-v4-pro-free",
            ),
            capabilities=(
                "tool_calling",
            ),
            priority=34,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-orca-v4-flash",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "ORCA_DEEPSEEK_V4_FLASH_MODEL",
                default="deepseek/deepseek-v4-flash",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=32,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-orca-v4-flash-free",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "ORCA_DEEPSEEK_V4_FLASH_FREE_MODEL",
                default="deepseek/deepseek-v4-flash-free",
            ),
            capabilities=(
                "tool_calling",
            ),
            priority=33,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-orca-v4-flash-0731",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "ORCA_DEEPSEEK_V4_FLASH_0731_MODEL",
                default="deepseek/deepseek-v4-flash-0731",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=31,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-orca-luna",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "ORCA_OPENAI_5.6_LUNA_MODEL",
                default="openai/gpt-5.6-luna",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=30,

        )
    )

    # Non-requesty verification/evaluation models so review and rubric
    # grading survive a requesty 402 (payment) disable. Priorities sit
    # below the requesty stage models so requesty is preferred while healthy.
    registry.register_model(
        ModelConfig(
            name="review-orca",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "REVIEW_ORCA_MODEL",
                "ORCA_DEEPSEEK_V4_FLASH_MODEL",
                default="deepseek/deepseek-v4-flash",
            ),
            capabilities=(
                "verification",
                "tool_calling",
            ),
            priority=40,
        )
    )

    registry.register_model(
        ModelConfig(
            name="grader-orca",
            provider="orcarouter",
            model_id=_resolve_model_id(
                "GRADER_ORCA_MODEL",
                "ORCA_DEEPSEEK_V4_FLASH_MODEL",
                default="deepseek/deepseek-v4-flash",
            ),
            capabilities=(
                "evaluation",
                "tool_calling",
            ),
            priority=40,
        )
    )






    # ---------------------------------------------------------
    # Bedrock models removed: IAM lacks model access ("Operation not
    # allowed" / invalid identifier) — re-add once access is granted.
    # Mantle GPT entries (gpt-5.6-luna/terra ARNs) removed pending
    # validation; Kimi/GLM/Minimax/Mistral Mantle models remain.
    # ---------------------------------------------------------

    # Mantle models (available on Mantle endpoint with Chat Completions API)
    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-kimi-k2-thinking",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_KIMI_K2_THINKING_MODEL",
                default="moonshotai.kimi-k2-thinking",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-kimi-k2-5",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_KIMI_K2_5_MODEL",
                default="moonshotai.kimi-k2.5",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,

            context_window=256000,
            input_cost_per_1m_tokens=0.60,
            output_cost_per_1m_tokens=2.50,
        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-glm-5",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_GLM_5_MODEL",
                default="zai.glm-5",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-glm-4-7",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_GLM_4_7_MODEL",
                default="zai.glm-4.7",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-mantle-minimax-m2",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_MINIMAX_M2_MODEL",
                default="minimax.minimax-m2",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-mantle-minimax-m2-5",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_MINIMAX_M2_5_MODEL",
                default="minimax.minimax-m2.5",
            ),
            # structured_output stripped: probe hit LengthFinishReasonError.
            capabilities=(
                "tool_calling",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-mistral-large-3",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_MISTRAL_LARGE_3_MODEL",
                default="mistral.mistral-large-3-675b-instruct",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-grok-4-3",
            provider="mantle-openai",
            model_id=_resolve_model_id(
                "MANTLE_GROK_4_3_MODEL",
                default="xai.grok-4.3",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-mantle-qwen3-coder-next",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_QWEN3_CODER_NEXT_MODEL",
                default="qwen.qwen3-coder-next",
            ),
            capabilities=(
                "tool_calling",
            ),
            priority=3,

        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-gemma-4-31b",
            provider="mantle-openai",
            model_id=_resolve_model_id(
                "MANTLE_GEMMA_4_31B_MODEL",
                default="google.gemma-4-31b",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,

        )
    )

    return ModelRouter(
        registry=registry,
        health=health,
        stats_store=stats_store or EMAStatsStore(),
        model_health=model_health or ModelHealthRegistry(),
        enabled_providers=enabled_providers,
    )


# role -> (chain_key, routing capability, capability tuple).
# ``chain_key`` selects the FALLBACKS chain (FALLBACKS namespace:
# reasoning/research/fast/verification/evaluation — `tool_calling`
# roles use the fast chain). ``capability`` is the routing
# capability the per-call middleware resolves (KNOWN_CAPABILITIES
# namespace; Task 18 consumes these policies).
ROLE_POLICIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "documentation_engineer",
        "reasoning",
        "reasoning",
        ("reasoning", "tool_calling", "structured_output"),
    ),
    (
        "documentation_reviewer",
        "verification",
        "verification",
        ("verification", "tool_calling"),
    ),
    ("github_intelligence", "research", "research", ("research", "tool_calling")),
    ("support_engineer", "fast", "tool_calling", ("tool_calling",)),
    ("support_reviewer", "verification", "verification", ("verification", "tool_calling")),
    ("research", "research", "research", ("research", "tool_calling")),
    ("deepeval", "evaluation", "evaluation", ("evaluation", "tool_calling")),
    ("github_delivery", "fast", "tool_calling", ("tool_calling",)),
    ("memory_curator", "fast", "tool_calling", ("tool_calling",)),
    ("classifier", "fast", "tool_calling", ("tool_calling",)),
    ("context", "research", "research", ("research", "tool_calling")),
    (
        "content_strategist", "research", "research",
        ("research", "tool_calling", "structured_output"),
    ),
    (
        "content_blog_writer", "reasoning", "reasoning",
        ("reasoning", "tool_calling", "structured_output"),
    ),
    (
        "content_social_adapter", "reasoning", "reasoning",
        ("reasoning", "tool_calling", "structured_output"),
    ),
)


PROVIDER_CLASSES = {
    "openrouter": OpenRouterProvider,
    "nvidia": NvidiaProvider,
    "requesty": RequestyProvider,
    "orcarouter": OrcaRouterProvider,
    "bedrock": BedrockProvider,
    "mantle": MantleProvider,
    "mantle-openai": MantleOpenAIProvider,
}

def build_agent_policies() -> dict[str, AgentModelPolicy]:
    """
    Build the per-role model policies used by Draftly agents.

    Each policy binds a role to a primary provider chain (from
    ``FALLBACKS``) and the routing capability that role's model must
    satisfy. Chains and capability names are validated so provider
    and capability typos fail fast at build time.
    """

    policies: dict[str, AgentModelPolicy] = {}

    for role, chain_key, capability, capabilities in ROLE_POLICIES:
        chain = FALLBACKS[chain_key]

        validate_fallback_chain(chain)
        CapabilityMatcher.validate_capability(capability)

        policies[role] = AgentModelPolicy(
            role=role,
            primary=chain,
            fallback=(),
            capabilities=capabilities,
            capability=capability,
            temperature=0.0,
        )

    return policies


def build_embedding_router() -> EmbeddingRouter:
    registry = ModelRegistry()

    health = ProviderHealthRegistry()

    for provider_name, api_key_var, base_url_var in (
        ("requesty", "REQUESTY_API_KEY", "REQUESTY_BASE_URL"),
        ("orcarouter", "ORCAROUTER_API_KEY", "ORCAROUTER_BASE_URL"),
        ("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"),
    ):
        api_key = os.getenv(api_key_var)

        if not api_key:
            logger.info(
                "embedding provider skipped provider=%s var=%s",
                provider_name,
                api_key_var,
            )
            continue

        registry.register_provider(
            PROVIDER_CLASSES[provider_name](  # type: ignore[abstract]
                ProviderConfig(
                    name=provider_name,
                    api_key=api_key,
                    base_url=os.getenv(base_url_var),
                )
            )
        )

    model_id = _resolve_model_id(
        "EMBEDDING_MODEL_ID",
        default="text-embedding-3-small",
    )

    for priority, provider_name in enumerate(
        ("openrouter", "requesty", "orcarouter"),
        start=10,
    ):
        if provider_name not in registry.providers():
            continue

        registry.register_embedding_model(
            EmbeddingConfig(
                name=f"embedding-{provider_name}",
                provider=provider_name,
                model_id=model_id,
                dimensions=1536,
                priority=priority,
            )
        )

    if not registry.list_embedding_models():
        raise RuntimeError(
            "No embedding provider is configured (set REQUESTY_API_KEY, "
            "ORCAROUTER_API_KEY, or OPENROUTER_API_KEY)."
        )

    return EmbeddingRouter(
        registry=registry,
        health=health,
    )
