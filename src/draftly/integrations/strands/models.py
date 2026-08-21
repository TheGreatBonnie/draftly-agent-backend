"""Model resolution for Strands agents and evaluators.

``build_model`` returns the configured model router (all agents accept
``Model | str | ModelRouter``). ``resolve_concrete_model`` picks one
concrete provider ``Model`` — required by Strands Evals LLM judges, which
do not accept routers (plan §13 risk).
"""

from __future__ import annotations

from typing import Any

from draftly.models.factory import build_model_router


def build_model() -> Any:
    """Build the Draftly model router from configured providers."""
    return build_model_router()


def resolve_concrete_model(router: Any = None, *, index: int = 0) -> Any:
    """Extract a concrete ``Model`` from a router for LLM-judge evaluators.

    Resolves through the router's registry (first enabled model by
    priority). Falls back to building a fresh router when none is supplied.
    Raises a descriptive error when no concrete model is available (e.g. no
    provider keys configured) so callers can degrade to deterministic
    evaluators.
    """
    if router is None:
        router = build_model_router()

    registry = getattr(router, "registry", None)
    configs = registry.list_models() if registry is not None else []
    if not configs:
        raise ValueError(
            "No enabled ModelConfig in the registry; configure at least "
            "one provider key or use deterministic evaluators."
        )

    config = configs[min(index, len(configs) - 1)]
    provider = registry.get_provider(config.provider)
    return provider.create_model(config)
