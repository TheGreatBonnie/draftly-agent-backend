"""Non-requesty verification/evaluation models must exist so the reviewer
and rubric grader survive a requesty 402 payment failure."""

from unittest.mock import MagicMock

from draftly.models.config import ModelConfig
from draftly.models.factory import build_model_router
from draftly.models.health import ProviderHealthRegistry
from draftly.models.policies import RoutingPolicy
from draftly.models.registry import ModelRegistry
from draftly.models.router import ModelRouter


def test_factory_registers_review_and_grader_on_orca():
    router = build_model_router()
    models = {m.name: m for m in router.registry.list_models()}
    assert "review-orca" in models
    assert models["review-orca"].provider == "orcarouter"
    assert "verification" in models["review-orca"].capabilities
    assert "grader-orca" in models
    assert models["grader-orca"].provider == "orcarouter"
    assert "evaluation" in models["grader-orca"].capabilities


def _minimal_router() -> ModelRouter:
    reg = ModelRegistry()

    def provider(name: str) -> MagicMock:
        p = MagicMock()
        p.name = name
        p.create_model.side_effect = lambda config: f"MODEL-{config.provider}"
        return p

    reg.register_provider(provider("requesty"))
    reg.register_provider(provider("orcarouter"))

    for name, provider_name, caps, prio in (
        ("review-model", "requesty", ("verification", "tool_calling"), 30),
        ("stage-review", "requesty", ("verification", "tool_calling"), 50),
        ("review-orca", "orcarouter", ("verification", "tool_calling"), 40),
        ("grader-orca", "orcarouter", ("evaluation", "tool_calling"), 40),
    ):
        reg.register_model(
            ModelConfig(
                name=name,
                provider=provider_name,
                model_id=f"org/{name}",
                capabilities=caps,
                priority=prio,
            )
        )
    return ModelRouter(reg, ProviderHealthRegistry())


def test_review_fails_over_to_orca_when_requesty_disabled():
    router = _minimal_router()
    router.health.get("requesty").disable()
    model = router.resolve(
        RoutingPolicy(
            required_capabilities=("verification",),
            allow_fallback=True,
        )
    )
    assert model == "MODEL-orcarouter"


def test_grade_fails_over_to_orca_when_requesty_disabled():
    router = _minimal_router()
    router.health.get("requesty").disable()
    model = router.resolve(
        RoutingPolicy(
            required_capabilities=("evaluation",),
            allow_fallback=True,
        )
    )
    assert model == "MODEL-orcarouter"