"""Tests for routing profiles."""

from typing import cast

import pytest

from draftly.models.profiles import ROUTING_PROFILES, get_profile
from draftly.models.schemas import TaskType


def test_all_task_types_have_profiles():
    for task_type in TaskType:
        profile = get_profile(task_type)
        assert profile is not None, f"Missing profile for {task_type}"


def test_profile_weights_sum_to_one():
    for name, p in ROUTING_PROFILES.items():
        total = p.w_quality + p.w_reliability + p.w_latency + p.w_cost + p.w_history
        assert abs(total - 1.0) < 1e-6, f"{name} weights sum to {total}, expected 1.0"


def test_spec_table_values():
    # Spec §Routing profiles table — dimensions (quality, reliability, latency, cost, history)
    support = ROUTING_PROFILES["support"]
    assert (support.w_quality, support.w_reliability, support.w_latency,
            support.w_cost, support.w_history) == pytest.approx((0.25, 0.15, 0.35, 0.25, 0.0))

    gen = ROUTING_PROFILES["documentation_generation"]
    assert (gen.w_quality, gen.w_reliability, gen.w_latency,
            gen.w_cost, gen.w_history) == pytest.approx((0.40, 0.25, 0.10, 0.10, 0.15))

    review = ROUTING_PROFILES["documentation_review"]
    assert (review.w_quality, review.w_reliability, review.w_latency,
            review.w_cost, review.w_history) == pytest.approx((0.50, 0.30, 0.05, 0.10, 0.05))
    assert review.w_quality > review.w_cost * 3  # reviews are NOT cost-optimized


def test_quality_floors():
    assert ROUTING_PROFILES["support"].quality_floor == pytest.approx(0.80)
    assert ROUTING_PROFILES["fast"].quality_floor == pytest.approx(0.80)
    assert ROUTING_PROFILES["documentation_generation"].quality_floor == pytest.approx(0.90)
    assert ROUTING_PROFILES["reasoning"].quality_floor == pytest.approx(0.88)
    assert ROUTING_PROFILES["documentation_review"].quality_floor == pytest.approx(0.93)
    assert ROUTING_PROFILES["evaluation"].quality_floor == pytest.approx(0.93)
    assert ROUTING_PROFILES["delivery"].quality_floor == pytest.approx(0.95)


def test_get_profile_unknown_task_returns_default():
    # Brief's verbatim test passed TaskType.SUPPORT, which IS mapped; use a
    # genuinely unmapped key to exercise the default path (runtime dict miss).
    unknown = cast(TaskType, "nonexistent")
    profile = get_profile(unknown, default=ROUTING_PROFILES["fast"])
    assert profile.name == "fast"
