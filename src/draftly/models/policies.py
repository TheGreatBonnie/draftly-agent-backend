from dataclasses import dataclass

__all__ = [
    "AgentModelPolicy",
    "FALLBACKS",
    "KNOWN_PROVIDERS",
    "RoutingPolicy",
    "validate_fallback_chain",
]

KNOWN_PROVIDERS = frozenset({"openrouter", "nvidia", "requesty", "orcarouter", "bedrock", "mantle"})

FALLBACKS: dict[str, tuple[str, ...]] = {
    "reasoning": (
        "mantle",
        "bedrock",
        "nvidia",
        "requesty",
        "orcarouter",
        "openrouter",
    ),
    "research": (
        "mantle",
        "bedrock",
        "nvidia",
        "requesty",
        "orcarouter",
        "openrouter",
    ),
    "fast": (
        "mantle",
        "bedrock",
        "nvidia",
        "requesty",
        "orcarouter",
        "openrouter",
    ),
    "verification": (
        "mantle",
        "bedrock",
        "nvidia",
        "requesty",
        "orcarouter",
        "openrouter",
    ),
    "evaluation": (
        "mantle",
        "bedrock",
        "nvidia",
        "requesty",
        "orcarouter",
        "openrouter",
    ),
}


def validate_fallback_chain(
    chain: tuple[str, ...],
) -> None:
    unknown = set(chain) - KNOWN_PROVIDERS
    if unknown:
        raise ValueError(
            f"Unknown provider(s) in fallback chain: {sorted(unknown)}. "
            f"Known providers: {sorted(KNOWN_PROVIDERS)}."
        )


@dataclass(frozen=True)
class RoutingPolicy:
    preferred_models: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    allow_fallback: bool = True
    max_attempts: int = 3
    fallback_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentModelPolicy:
    role: str
    primary: tuple[str, ...]
    fallback: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    temperature: float = 0.0
    capability: str = ""
    max_output_tokens: int = 2048
