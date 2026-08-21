"""EvaluatorNode with ContentBlock inputs matching the graph's format."""

from __future__ import annotations

import json

import pytest

from draftly.orchestration.nodes.base import parse_node_input
from draftly.orchestration.nodes.evaluate import EvaluatorNode, compute_quality


def _blocks(
    evidence: list[dict] | None = None,
    draft: str = "",
    *,
    source: str = "update",
) -> list[dict]:
    blocks = [
        {"text": "Original Task: task"},
        {"text": "\nInputs from previous nodes:"},
        {"text": "\nFrom research:"},
        {
            "text": f"  - Agent: {_json(evidence or [])}"
        },
        {"text": f"\nFrom {source}:"},
        {"text": f"  - WriterAgent: {_json({'draft': draft})}"},
    ]
    return blocks


def _json(data) -> str:
    import json

    return json.dumps(data)


class TestComputeQuality:
    def test_full_grounding(self) -> None:
        score, reasons = compute_quality(
            [{"id": "doc-1", "topic": "neon"}],
            "Neon is serverless postgres. See doc-1 for details.",
        )
        assert score >= 0.7
        assert any("Grounded in" in r for r in reasons)

    def test_empty_evidence_length_only(self) -> None:
        score, reasons = compute_quality([], "x" * 1000)
        assert score < 0.7
        assert reasons

    def test_missing_citations(self) -> None:
        score, _ = compute_quality(
            [{"id": "doc-1", "topic": "neon"}, {"id": "doc-2", "topic": "pg"}],
            "short draft",
        )
        assert score < 0.5


class TestEvaluatorNode:
    @pytest.mark.asyncio
    async def test_passing_draft(self) -> None:
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = (
            "Neon is a serverless Postgres platform. doc-1 "
            "doc-1 doc-1 doc-1 doc-1 doc-1 neon " * 12
        )
        node = EvaluatorNode()
        result = await node.invoke_async(_blocks(evidence, draft))
        payload = result.results["evaluate"].result.message["content"][0]["text"]

        data = json.loads(payload)
        assert set(data) == {"passed", "score", "reasons", "iteration"}
        assert data["passed"] is True
        assert data["score"] >= 0.7
        assert data["iteration"] == 1

    @pytest.mark.asyncio
    async def test_failing_draft_then_iteration_cap(self) -> None:
        node = EvaluatorNode(max_iterations=2)
        blocks = _blocks([], "tiny")

        first = await node.invoke_async(blocks)
        first_data = json.loads(
            first.results["evaluate"].result.message["content"][0]["text"]
        )
        assert first_data["passed"] is False

        second = await node.invoke_async(blocks)
        second_data = json.loads(
            second.results["evaluate"].result.message["content"][0]["text"]
        )
        assert second_data["passed"] is True  # iteration >= max_iterations
        assert second_data["iteration"] == 2

    @pytest.mark.asyncio
    async def test_draft_from_answer_node(self) -> None:
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = "Answer text neon doc-1 " * 30
        node = EvaluatorNode()
        result = await node.invoke_async(_blocks(evidence, draft, source="answer"))
        payload = json.loads(
            result.results["evaluate"].result.message["content"][0]["text"]
        )
        assert payload["score"] >= 0.7

    @pytest.mark.asyncio
    async def test_plain_text_research_degrades_safely(self) -> None:
        """Non-JSON research output → evidence=[]; scoring still runs."""
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": "  - ResearchAgent: some plain text summary"},
            {"text": "\nFrom update:"},
            {"text": "  - WriterAgent: " + _json({"draft": "tiny"})},
        ]
        node = EvaluatorNode()
        result = await node.invoke_async(blocks)
        payload = json.loads(
            result.results["evaluate"].result.message["content"][0]["text"]
        )
        assert payload["passed"] is False
        assert payload["score"] < 0.7

    @pytest.mark.asyncio
    async def test_parse_node_input_shares_format(self) -> None:
        blocks = _blocks([{"id": "doc-1"}], "draft text")
        deps = parse_node_input(blocks)
        assert "research" in deps
        assert "update" in deps
        assert deps["update"]["draft"] == "draft text"
