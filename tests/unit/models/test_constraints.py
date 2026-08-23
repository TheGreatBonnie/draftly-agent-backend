"""Tests for the constraint pipeline."""


from draftly.models.config import ModelConfig
from draftly.models.constraints import ConstraintPipeline
from draftly.models.health import ProviderHealthRegistry
from draftly.models.performance import EMAStatsStore, ModelHealthRegistry
from draftly.models.schemas import RoutingRequest, TaskType


def _make_model(name, provider="openrouter", ctx_window=None, caps=("text",)):
    return ModelConfig(
        name=name,
        provider=provider,
        model_id=f"org/{name}",
        context_window=ctx_window,
        capabilities=caps,
    )


def _make_request(task_type=TaskType.SUPPORT, context_tokens=1000,
                  cost_budget=None, latency_budget_ms=None):
    return RoutingRequest(task_type=task_type, context_tokens=context_tokens,
                          estimated_output_tokens=500,
                          cost_budget=cost_budget, latency_budget_ms=latency_budget_ms)


def _filter(req, models, *, provider_health=None, model_health=None,
            enabled=("openrouter",), required_caps=None, stats_store=None):
    pipeline = ConstraintPipeline()
    return pipeline.filter_candidates(
        req, models,
        provider_health=provider_health or ProviderHealthRegistry(),
        model_health=model_health or ModelHealthRegistry(),
        enabled_providers=set(enabled),
        required_caps=required_caps,
        stats_store=stats_store,
    )


def test_provider_enabled_constraint():
    models = [_make_model("a"), _make_model("b", provider="unknown")]
    result = _filter(_make_request(), models)
    assert [m.name for m in result] == ["a"]


def test_context_window_constraint():
    # Declared window smaller than request -> excluded
    models = [_make_model("small", ctx_window=8192)]
    assert _filter(_make_request(context_tokens=200000), models) == []
    # Unknown window passes only small-context requests
    unknown = [_make_model("mystery", ctx_window=None)]
    assert len(_filter(_make_request(context_tokens=200000), unknown)) == 0
    assert len(_filter(_make_request(context_tokens=4000), unknown)) == 1


def test_capabilities_constraint():
    models = [_make_model("a", caps=("text",))]
    assert _filter(_make_request(), models, required_caps={"vision"}) == []


def test_provider_health_uses_real_registry():
    models = [_make_model("a")]
    provider_health = ProviderHealthRegistry()
    provider_health.get("openrouter").disable()
    assert _filter(_make_request(), models, provider_health=provider_health) == []


def test_model_cooldown_constraint():
    models = [_make_model("a")]
    model_health = ModelHealthRegistry(cooldown_seconds=60)
    model_health.mark_failure("a")
    assert _filter(_make_request(), models, model_health=model_health) == []


def test_quality_floor_eliminates_known_bad_models_only():
    models = [_make_model("weak"), _make_model("strong"), _make_model("novice")]
    store = EMAStatsStore()
    for _ in range(20):  # reach the >=20 sample gate
        store.record_quality("support", "weak", 0.70)
        store.record_quality("support", "strong", 0.95)
        # "novice" has no samples -> floor NOT applied to it
    result = _filter(_make_request(), models, stats_store=store)
    assert {m.name for m in result} == {"strong", "novice"}


def test_quality_floor_gated_on_sample_threshold():
    """Below MIN_QUALITY_SAMPLES, low quality is untrustworthy and must NOT eliminate."""
    models = [_make_model("young_weak"), _make_model("seasoned_weak")]
    store = EMAStatsStore()
    for i in range(20):
        store.record_quality("support", "seasoned_weak", 0.70)
        if i < 10:  # only 10 samples -> below the >=20 trust threshold
            store.record_quality("support", "young_weak", 0.70)
    result = _filter(_make_request(), models, stats_store=store)
    assert {m.name for m in result} == {"young_weak"}


def test_soft_cost_relaxation_keeps_candidates():
    models = [_make_model("pricey")]
    result = _filter(_make_request(cost_budget=0.000001), models)
    # Soft constraint relaxes rather than emptying the candidate set
    assert len(result) == 1
