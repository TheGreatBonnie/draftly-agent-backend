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
    configs = list(registry.list_models()) if registry is not None else []
    if not configs or registry is None:
        raise ValueError(
            "No enabled ModelConfig in the registry; configure at least "
            "one provider key or use deterministic evaluators."
        )

    config = configs[min(index, len(configs) - 1)]
    provider = registry.get_provider(config.provider)
    return provider.create_model(config)


class RoleAwareModelResolver:
    """Resolves a concrete Strands model PER AGENT ROLE via route()."""

    def __init__(self, router: Any) -> None:
        self._router = router

    def for_role(
        self,
        role: str,
        *,
        prompt_text: str | None = None,
        context_tokens: int | None = None,
    ) -> Any:
        from draftly.models.schemas import ROLE_TO_TASK_TYPE, RoutingRequest

        try:
            task_type = ROLE_TO_TASK_TYPE[role]
        except KeyError:
            raise ValueError(
                f"Unknown role '{role}'; add it to ROLE_TO_TASK_TYPE."
            ) from None

        tokens = context_tokens or _estimate_tokens(prompt_text)
        request = RoutingRequest(task_type=task_type, context_tokens=tokens)
        decision = self._router.route(request)

        config = self._router.registry.get_model(decision.selected_model)
        provider = self._router.registry.get_provider(decision.provider)
        return provider.create_model(config)


def _estimate_tokens(prompt_text: str | None) -> int:
    """~4 chars/token heuristic (spec: token estimates from prompt length)."""
    if not prompt_text:
        return 4096
    return max(1024, len(prompt_text) // 4)


def resolve_model_for_role(model_or_resolver: Any, role: str) -> Any:
    """Graph-builder helper: per-role model when a resolver is present,
    otherwise the shared model verbatim (legacy builders untouched)."""
    if hasattr(model_or_resolver, "for_role"):
        return model_or_resolver.for_role(role)
    return model_or_resolver
