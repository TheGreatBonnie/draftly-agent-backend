"""Conditions behave defensively on simulated GraphState."""

from __future__ import annotations

import json

from strands.multiagent.base import MultiAgentResult, NodeResult, Status
from strands.multiagent.graph import GraphState

from draftly.orchestration.nodes.base import agent_result
from draftly.orchestration.routing.conditions import (
    all_dependencies_complete,
    eval_passed,
    generated,
    is_valid_surface,
    needs_revision,
    route_to_answer,
    route_to_create,
    route_to_update,
)


def _state_with_results(results: dict, node_status: Status = Status.COMPLETED) -> GraphState:
    state = GraphState()
    for node_id, result in results.items():
        state.results[node_id] = NodeResult(result=result, status=node_status)
    return state


def _impact_result(action: str) -> MultiAgentResult:
    return MultiAgentResult(
        status=Status.COMPLETED,
        results={
            "impact": NodeResult(
                result=agent_result({"action": action, "docs": []}),
            )
        },
    )


def _evaluate_result(passed: bool) -> MultiAgentResult:
    return MultiAgentResult(
        status=Status.COMPLETED,
        results={
            "evaluate": NodeResult(
                result=agent_result({"passed": passed, "score": 0.9 if passed else 0.3}),
            )
        },
    )


class TestIsValidSurface:
    def test_recognized_surface(self) -> None:
        state = GraphState(task=json.dumps({"event_type": "pull_request.opened"}))
        assert is_valid_surface(state)

    def test_unrecognized_surface(self) -> None:
        state = GraphState(task=json.dumps({"event_type": "weird.thing"}))
        assert not is_valid_surface(state)

    def test_non_string_task(self) -> None:
        state = GraphState(task=["not", "a", "string"])
        assert not is_valid_surface(state)

    def test_invalid_json(self) -> None:
        state = GraphState(task="not json at all")
        assert not is_valid_surface(state)

    def test_empty_task(self) -> None:
        state = GraphState(task="")
        assert not is_valid_surface(state)


class TestRouteToAction:
    def test_route_to_answer(self) -> None:
        state = _state_with_results({"impact": _impact_result("answer")})
        assert route_to_answer(state)
        assert not route_to_update(state)
        assert not route_to_create(state)

    def test_route_to_update(self) -> None:
        state = _state_with_results({"impact": _impact_result("update")})
        assert route_to_update(state)
        assert not route_to_answer(state)
        assert not route_to_create(state)

    def test_route_to_create(self) -> None:
        state = _state_with_results({"impact": _impact_result("create")})
        assert route_to_create(state)
        assert not route_to_answer(state)
        assert not route_to_update(state)

    def test_missing_impact_is_safe(self) -> None:
        state = GraphState()
        assert not route_to_answer(state)
        assert not route_to_update(state)
        assert not route_to_create(state)

    def test_unexpected_action(self) -> None:
        state = _state_with_results({"impact": _impact_result("explode")})
        assert not route_to_answer(state)
        assert not route_to_update(state)
        assert not route_to_create(state)


class TestGenerated:
    def test_any_generation_node_present(self) -> None:
        state = _state_with_results({"update": _impact_result("update")})
        assert generated(state)

    def test_no_generation_nodes(self) -> None:
        state = _state_with_results(
            {"impact": _impact_result("update"), "research": _impact_result("answer")}
        )
        assert not generated(state)

    def test_empty_state(self) -> None:
        assert not generated(GraphState())


class TestEvaluationConditions:
    def test_eval_passed(self) -> None:
        state = _state_with_results({"evaluate": _evaluate_result(True)})
        assert eval_passed(state)
        assert not needs_revision(state)

    def test_needs_revision(self) -> None:
        state = _state_with_results({"evaluate": _evaluate_result(False)})
        assert needs_revision(state)
        assert not eval_passed(state)

    def test_missing_evaluate_is_safe(self) -> None:
        state = GraphState()
        assert not eval_passed(state)
        assert not needs_revision(state)


class TestAllDependenciesComplete:
    def test_all_complete(self) -> None:
        state = _state_with_results(
            {
                "impact": _impact_result("update"),
                "research": _impact_result("update"),
            }
        )
        check = all_dependencies_complete(["impact", "research"])
        assert check(state)

    def test_partial(self) -> None:
        state = _state_with_results({"impact": _impact_result("update")})
        check = all_dependencies_complete(["impact", "research"])
        assert not check(state)

    def test_failed_node_blocks(self) -> None:
        state = _state_with_results(
            {"impact": _impact_result("update")},
            node_status=Status.FAILED,
        )
        check = all_dependencies_complete(["impact"])
        assert not check(state)
