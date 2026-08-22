import logging
import os

from .capabilities import CapabilityMatcher
from .config import EmbeddingConfig, ModelConfig, ProviderConfig
from .embeddings import EmbeddingRouter
from .health import ProviderHealthRegistry
from .policies import (
    FALLBACKS,
    AgentModelPolicy,
    validate_fallback_chain,
)
from .providers import (
    BedrockProvider,
    MantleProvider,
    NvidiaProvider,
    OpenRouterProvider,
    OrcaRouterProvider,
    RequestyProvider,
)
from .registry import ModelRegistry
from .router import ModelRouter

logger = logging.getLogger(__name__)


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


def build_model_router() -> ModelRouter:

    registry = ModelRegistry()

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

    # ---------------------------------------------------------
    # Models
    # ---------------------------------------------------------

    registry.register_model(
        ModelConfig(
            name="reasoning-nvidia",
            provider="nvidia",
            model_id=_resolve_model_id(
                "NVIDIA_REASONING_MODEL",
                "GLM_5.2_MODEL",
                default="moonshotai/kimi-k2.5",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=10,
            max_tokens=4096,
        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-nvidia",
            provider="nvidia",
            model_id=_resolve_model_id(
                "NVIDIA_FAST_MODEL",
                "NVIDIA_DEEPSEEK_V4_FLASH_MODEL",
                default="deepseek-ai/deepseek-v4-flash",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=10,
            max_tokens=2048,
        )
    )

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
            max_tokens=4096,
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
            max_tokens=2048,
        )
    )

    stage_models = (
        (
            "stage-research",
            "RESEARCH_MODEL",
            "tensorx/deepseek-v4-flash",
            ("research", "tool_calling"),
            2048,
        ),
        (
            "stage-review",
            "REVIEW_MODEL",
            "tensorx/deepseek-v4-flash",
            ("verification", "tool_calling"),
            2048,
        ),
        (
            "stage-rubric-grader",
            "RUBRIC_GRADER_MODEL",
            "tensorx/deepseek-v4-flash",
            ("evaluation", "tool_calling"),
            2048,
        ),
    )

    for stage_name, env_var, default_model, capabilities, stage_max_tokens in stage_models:
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
                max_tokens=stage_max_tokens,
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
            max_tokens=2048,
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
            max_tokens=2048,
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
            max_tokens=2048,
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
            max_tokens=4096,
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
            max_tokens=4096,
        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-openrouter",
            provider="openrouter",
            model_id=_resolve_model_id(
                "OPENROUTER_FAST_MODEL",
                "LAGUNA_MODEL",
                default="google/gemini-2.5-flash",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=40,
            max_tokens=2048,
        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-openrouter",
            provider="openrouter",
            model_id=_resolve_model_id(
                "OPENROUTER_FAST_MODEL",
                "LAGUNA_MODEL",
                default="google/gemini-2.5-flash",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=40,
            max_tokens=2048,
        )
    )

    # Bedrock models
    registry.register_model(
        ModelConfig(
            name="reasoning-bedrock-nova",
            provider="bedrock",
            model_id=_resolve_model_id(
                "BEDROCK_NOVA_REASONING_MODEL",
                default="amazon.nova-pro-v1:0",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=5,
            max_tokens=4096,
        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-bedrock-nova",
            provider="bedrock",
            model_id=_resolve_model_id(
                "BEDROCK_NOVA_FAST_MODEL",
                default="amazon.nova-micro-v1:0",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=5,
            max_tokens=4096,
        )
    )

    registry.register_model(
        ModelConfig(
            name="reasoning-bedrock-claude",
            provider="bedrock",
            model_id=_resolve_model_id(
                "BEDROCK_CLAUDE_REASONING_MODEL",
                "BEDROCK_CLAUDE_SONNET_4_MODEL",
                default="global.anthropic.claude-sonnet-4-6",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=5,
            max_tokens=8192,
        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-bedrock-claude",
            provider="bedrock",
            model_id=_resolve_model_id(
                "BEDROCK_CLAUDE_FAST_MODEL",
                "BEDROCK_CLAUDE_HAIKU_MODEL",
                default="global.anthropic.claude-3-5-haiku-20241022-v1:0",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=5,
            max_tokens=4096,
        )
    )

    # Mantle models (OpenAI-compatible on Bedrock)
    # Note: GPT-5.6 models require inference profile ARNs
    # OSS models (gpt-oss-120b/20b) are ON_DEMAND but may need model access enabled
    # Other models (Kimi, GLM, Minimax, Mistral) work on Mantle with Chat Completions API
    registry.register_model(
        ModelConfig(
            name="reasoning-mantle-gpt",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_REASONING_MODEL",
                "NEMOTRON_3_ULTRA_MODEL",
                default="arn:aws:bedrock:us-east-1:145776961336:inference-profile/global.openai.gpt-5.6-luna",
            ),
            capabilities=(
                "reasoning",
                "tool_calling",
                "structured_output",
            ),
            priority=3,
            max_tokens=8192,
        )
    )

    registry.register_model(
        ModelConfig(
            name="fast-mantle-gpt",
            provider="mantle",
            model_id=_resolve_model_id(
                "MANTLE_FAST_MODEL",
                "LAGUNA_MODEL",
                default="arn:aws:bedrock:us-east-1:145776961336:inference-profile/global.openai.gpt-5.6-terra",
            ),
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=3,
            max_tokens=4096,
        )
    )

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
            max_tokens=8192,
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
            max_tokens=8192,
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
            max_tokens=8192,
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
            max_tokens=8192,
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
            max_tokens=4096,
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
            capabilities=(
                "tool_calling",
                "structured_output",
            ),
            priority=3,
            max_tokens=4096,
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
            max_tokens=8192,
        )
    )

    return ModelRouter(
        registry=registry,
        health=health,
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
)


PROVIDER_CLASSES = {
    "openrouter": OpenRouterProvider,
    "nvidia": NvidiaProvider,
    "requesty": RequestyProvider,
    "orcarouter": OrcaRouterProvider,
    "bedrock": BedrockProvider,
    "mantle": MantleProvider,
}

ROLE_OUTPUT_TOKENS: dict[str, int] = {
    "documentation_engineer": 4096,
    "documentation_reviewer": 4096,
    "support_engineer": 2048,
    "support_reviewer": 2048,
    "github_intelligence": 2048,
    "research": 2048,
    "deepeval": 4096,
    "github_delivery": 2048,
    "memory_curator": 1024,
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
            max_output_tokens=ROLE_OUTPUT_TOKENS.get(role, 2048),
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
        ("requesty", "orcarouter", "openrouter"),
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
