"""Tests for routing schemas."""

import pytest

from draftly.models.schemas import ROLE_TO_TASK_TYPE, RoutingDecision, RoutingRequest, TaskType


def test_task_type_values():
    assert TaskType.SUPPORT.value == "support"
    assert TaskType.FAST.value == "fast"
    assert TaskType.REASONING.value == "reasoning"
    assert TaskType.RESEARCH.value == "research"
    assert TaskType.DOCUMENTATION_GENERATION.value == "documentation_generation"
    assert TaskType.DOCUMENTATION_REVIEW.value == "documentation_review"
    assert TaskType.EVALUATION.value == "evaluation"
    assert TaskType.DELIVERY.value == "delivery"


def test_role_map_covers_all_agent_roles():
    expected_roles = {
        "documentation_engineer", "documentation_reviewer", "github_intelligence",
        "support_engineer", "support_reviewer", "research", "deepeval",
        "github_delivery", "memory_curator",
        # Every-agent-a-role wiring (graph builders resolve these too)
        "classifier", "context",
        # Onboarding init stages (Task 4)
        "knowledge_extractor", "initial_evaluator", "recommender",
        "content_strategist", "content_blog_writer", "content_social_adapter",
        # In-graph grounding judge (content surface)
        "content_judge",
    }
    assert set(ROLE_TO_TASK_TYPE) == expected_roles
    # Sensible mappings per reference §34 / existing role policies
    assert ROLE_TO_TASK_TYPE["documentation_engineer"] is TaskType.DOCUMENTATION_GENERATION
    assert ROLE_TO_TASK_TYPE["support_engineer"] is TaskType.SUPPORT
    assert ROLE_TO_TASK_TYPE["github_intelligence"] is TaskType.RESEARCH
    assert ROLE_TO_TASK_TYPE["deepeval"] is TaskType.EVALUATION
    assert ROLE_TO_TASK_TYPE["memory_curator"] is TaskType.FAST
    assert ROLE_TO_TASK_TYPE["classifier"] is TaskType.FAST
    assert ROLE_TO_TASK_TYPE["context"] is TaskType.RESEARCH
    assert ROLE_TO_TASK_TYPE["knowledge_extractor"] is TaskType.DOCUMENTATION_GENERATION
    assert ROLE_TO_TASK_TYPE["initial_evaluator"] is TaskType.EVALUATION
    assert ROLE_TO_TASK_TYPE["recommender"] is TaskType.DOCUMENTATION_REVIEW


def test_routing_request_is_frozen():
    req = RoutingRequest(task_type=TaskType.SUPPORT, context_tokens=500)
    with pytest.raises(AttributeError):
        setattr(req, "context_tokens", 1000)


def test_routing_request_defaults():
    req = RoutingRequest(task_type=TaskType.FAST, context_tokens=100)
    assert req.priority == 5
    assert req.cost_budget is None
    assert req.latency_budget_ms is None
    assert req.estimated_output_tokens == 1024
    assert req.metadata == {}


def test_routing_decision_is_frozen():
    dec = RoutingDecision(
        selected_model="a",
        provider="b",
        score=0.8,
        candidates_considered=5,
        profile="support",
    )
    with pytest.raises(AttributeError):
        setattr(dec, "selected_model", "c")


def test_routing_decision_debugging_fields():
    dec = RoutingDecision(
        selected_model="a", provider="b", score=0.9,
        candidates_considered=3, profile="support",
        ranked=(("a", 0.9), ("c", 0.7)),
        reason_codes=("capability_match", "healthy_provider"),
        fallback_chain=("c",),
    )
    assert dec.ranked[1] == ("c", 0.7)
    assert "capability_match" in dec.reason_codes
