"""Tests for spec-dimension weighted scoring."""


from draftly.models.config import ModelConfig
from draftly.models.performance import EMAStatsStore
from draftly.models.profiles import ROUTING_PROFILES
from draftly.models.schemas import RoutingRequest, TaskType
from draftly.models.scoring import score_candidates


def _make_model(name, provider="openrouter", priority=100):
    return ModelConfig(
        name=name, provider=provider, model_id=f"org/{name}",
        context_window=128000,
        input_cost_per_1m_tokens=0.50,
        output_cost_per_1m_tokens=1.50,
        priority=priority,
    )


def _request(task_type=TaskType.SUPPORT, **kw):
    return RoutingRequest(task_type=task_type, context_tokens=4000,
                          estimated_output_tokens=500, **kw)


def _store_with_quality(task_type, name, quality):
    store = EMAStatsStore()
    for _ in range(25):  # cross the >=20 sample gate
        store.record_quality(task_type, name, quality)
    return store


def test_scoring_returns_sorted_descending():
    models = [_make_model("a"), _make_model("b"), _make_model("c")]
    result = score_candidates(_request(), models, ROUTING_PROFILES["support"], EMAStatsStore())
    scores = [s for _, s in result]
    assert scores == sorted(scores, reverse=True)


def test_quality_used_only_after_sample_gate():
    # Below the gate: conservative default (= floor) drives quality
    cold_store = EMAStatsStore()
    cold_store.record_quality("support", "a", 0.99)  # 1 sample -> ignored
    req = _request()
    scored_cold = {m.name: s for m, s in score_candidates(
        req, [_make_model("a")], ROUTING_PROFILES["support"], cold_store)}
    warm_store = _store_with_quality("support", "a", 0.99)
    scored_warm = {m.name: s for m, s in score_candidates(
        req, [_make_model("a")], ROUTING_PROFILES["support"], warm_store)}
    assert scored_warm["a"] > scored_cold["a"]


def test_priority_breaks_score_ties_deterministically():
    low = _make_model("cheap-priority-1", priority=1)
    high = _make_model("lazy-priority-90", priority=90)
    store = EMAStatsStore()  # identical cold stats -> identical scores
    result = score_candidates(_request(), [high, low], ROUTING_PROFILES["support"], store)
    assert [m.name for m, _ in result] == ["cheap-priority-1", "lazy-priority-90"]


def test_history_weight_rewards_seasoned_models():
    fresh, seasoned = _make_model("fresh"), _make_model("seasoned")
    store = EMAStatsStore()
    for _ in range(120):
        store.record_outcome("support", "seasoned", success=True, latency_ms=900.0)
    scores = {m.name: s for m, s in score_candidates(
        _request(), [fresh, seasoned], ROUTING_PROFILES["support"], store)}
    assert scores["seasoned"] > scores["fresh"]


def test_zero_candidates():
    assert score_candidates(_request(), [], ROUTING_PROFILES["support"], EMAStatsStore()) == []
