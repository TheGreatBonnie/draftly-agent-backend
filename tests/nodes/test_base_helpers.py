"""agent_result / node_data / parse_node_input round-trips."""

from __future__ import annotations

import json

from strands.multiagent.base import MultiAgentResult, NodeResult, Status
from strands.multiagent.graph import GraphState

from draftly.orchestration.nodes.base import (
    agent_result,
    node_data,
    original_task,
    parse_node_input,
)


def _state_with_node(node_id: str, result: MultiAgentResult) -> GraphState:
    state = GraphState()
    state.results[node_id] = NodeResult(result=result)
    return state


class TestAgentResultRoundTrip:
    def test_round_trip_via_multi_agent_result(self) -> None:
        payload = {"passed": True, "score": 0.85, "reasons": ["ok"]}
        wrapped = MultiAgentResult(
            status=Status.COMPLETED,
            results={"evaluate": NodeResult(result=agent_result(payload))},
        )
        state = _state_with_node("evaluate", wrapped)
        assert node_data(state, "evaluate") == payload

    def test_nested_payload(self) -> None:
        payload = {"evidence": [{"id": "doc-1"}], "action": "update"}
        wrapped = MultiAgentResult(
            status=Status.COMPLETED,
            results={"impact": NodeResult(result=agent_result(payload))},
        )
        state = _state_with_node("impact", wrapped)
        assert node_data(state, "impact")["action"] == "update"


class TestParseNodeInput:
    def _blocks(self) -> list[dict]:
        return [
            {"text": "Original Task: some task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": '  - Agent: {"evidence": [{"id": "doc-1"}]}'},
            {"text": "\nFrom update:"},
            {"text": '  - WriterAgent: {"draft": "content here"}'},
        ]

    def test_extracts_per_dependency(self) -> None:
        parsed = parse_node_input(self._blocks())
        assert parsed == {
            "research": {"evidence": [{"id": "doc-1"}]},
            "update": {"draft": "content here"},
        }

    def test_agent_name_variants(self) -> None:
        blocks = [
            {"text": "\nFrom impact:"},
            {"text": '  - ImpactAgent: {"action": "create"}'},
            {"text": "\nFrom answer:"},
            {"text": '  - Agent: {"draft": "a draft"}'},
        ]
        parsed = parse_node_input(blocks)
        assert parsed["impact"] == {"action": "create"}
        assert parsed["answer"] == {"draft": "a draft"}

    def test_non_json_payload_skipped(self) -> None:
        blocks = [
            {"text": "\nFrom research:"},
            {"text": "  - ResearchAgent: plain text summary, no JSON"},
        ]
        assert parse_node_input(blocks) == {}

    def test_invalid_json_skipped(self) -> None:
        blocks = [
            {"text": "\nFrom research:"},
            {"text": "  - Agent: {broken json"},
        ]
        assert parse_node_input(blocks) == {}

    def test_raw_string_returns_empty(self) -> None:
        assert parse_node_input("just a raw task string") == {}

    def test_none_returns_empty(self) -> None:
        assert parse_node_input(None) == {}

    def test_no_dependency_sections(self) -> None:
        blocks = [{"text": "Original Task: only"}]
        assert parse_node_input(blocks) == {}

    def test_json_round_trip_through_agent_result(self) -> None:
        payload = {"draft": "body", "passed": True}
        wrapped = MultiAgentResult(
            status=Status.COMPLETED,
            results={"update": NodeResult(result=agent_result(payload))},
        )
        state = _state_with_node("update", wrapped)
        parsed = parse_node_input(
            [
                {"text": "\nFrom update:"},
                {"text": f"  - Agent: {json.dumps(node_data(state, 'update'))}"},
            ]
        )
        assert parsed["update"] == payload


class TestOriginalTask:
    TASK = json.dumps(
        {
            "event_type": "pull_request.opened",
            "repository": "acme/api",
            "pull_request": {"number": 7},
        }
    )

    def test_raw_string_event(self) -> None:
        assert original_task(self.TASK)["repository"] == "acme/api"

    def test_list_blocks_with_inputs_section(self) -> None:
        blocks = [
            {"text": f"Original Task: {self.TASK}"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom impact:"},
            {"text": '  - Agent: {"action": "none"}'},
        ]
        event = original_task(blocks)
        assert event["repository"] == "acme/api"
        assert event["pull_request"]["number"] == 7

    def test_invalid_json_returns_empty(self) -> None:
        assert original_task("not json") == {}

    def test_none_returns_empty(self) -> None:
        assert original_task(None) == {}

    def test_list_without_marker_returns_empty(self) -> None:
        assert original_task([{"text": "\nFrom impact:"}]) == {}
