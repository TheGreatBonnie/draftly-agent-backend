from collections.abc import Iterable

from .config import ModelConfig

__all__ = ["CapabilityMatcher", "KNOWN_CAPABILITIES"]

# Canonical capability names. New capabilities must be added here and
# as a constant on CapabilityMatcher so typos fail fast.
KNOWN_CAPABILITIES = frozenset(
    {
        "reasoning",
        "tool_calling",
        "structured_output",
        "research",
        "verification",
        "support",
        "evaluation",
    }
)


class CapabilityMatcher:
    """
    Canonical capability names and model selection.

    Agents request capabilities by name; the matcher decides
    whether a registered model can satisfy them. Unknown capability
    names are rejected at validation time so configuration typos
    surface as errors instead of silently matching nothing.
    """

    REASONING = "reasoning"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    RESEARCH = "research"
    VERIFICATION = "verification"
    SUPPORT = "support"
    EVALUATION = "evaluation"

    @staticmethod
    def validate_capability(capability: str) -> str:
        if capability not in KNOWN_CAPABILITIES:
            raise ValueError(
                f"Unknown capability: '{capability}'. "
                f"Known capabilities: {sorted(KNOWN_CAPABILITIES)}."
            )

        return capability

    def supports(
        self,
        model: ModelConfig,
        *required: str,
    ) -> bool:
        model_capabilities = set(model.capabilities)

        return all(
            capability in model_capabilities
            for capability in required
        )

    def filter(
        self,
        models: Iterable[ModelConfig],
        *required: str,
    ) -> list[ModelConfig]:
        return [
            model
            for model in models
            if self.supports(model, *required)
        ]
