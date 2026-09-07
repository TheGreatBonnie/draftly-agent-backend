"""EvaluatorNode with ContentBlock inputs matching the graph's format."""

from __future__ import annotations

import json

import pytest
from strands.agent.agent_result import AgentResult

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
        {"text": f"  - Agent: {_json(evidence or [])}"},
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

    def test_full_path_evidence_matches_when_draft_links_basename(self) -> None:
        """A draft that references a source doc by its file name (not the
        exact full-id byte string) must still count as citing that evidence."""
        evidence = [
            {
                "id": "docs/how-to/oauth-authorization-url",
                "topic": "authentication",
                "url": "authly/docs/how-to/oauth-authorization-url.md",
            }
        ]
        draft = (
            "OAuth authentication: build an authorization URL, then link the "
            "reader to docs/how-to/oauth-authorization-url.\n"
        ) * 8
        score, reasons = compute_quality(evidence, draft)
        assert score >= 0.9
        assert any("Grounded in" in r for r in reasons)

    def test_evidence_without_extension_still_matches(self) -> None:
        """id without .md suffix is matched when the draft contains the basename."""
        evidence = [
            {
                "id": "docs/topics/authentication",
                "topic": "authentication",
            }
        ]
        draft = ("Documenting authentication for the SDK. authentication " * 10)
        score, _ = compute_quality(evidence, draft)
        assert score >= 0.6


class TestEvaluatorNode:
    @pytest.mark.asyncio
    async def test_passing_draft(self) -> None:
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = (
            "Neon is a serverless Postgres platform. doc-1 doc-1 doc-1 doc-1 doc-1 doc-1 neon " * 12
        )
        node = EvaluatorNode()
        result = await node.invoke_async(_blocks(evidence, draft))
        node_result = result.results["evaluate"].result
        assert isinstance(node_result, AgentResult)
        payload = node_result.message["content"][0]["text"]

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
        first_result = first.results["evaluate"].result
        assert isinstance(first_result, AgentResult)
        first_data = json.loads(first_result.message["content"][0]["text"])
        assert first_data["passed"] is False

        second = await node.invoke_async(blocks)
        second_result = second.results["evaluate"].result
        assert isinstance(second_result, AgentResult)
        second_data = json.loads(second_result.message["content"][0]["text"])
        assert second_data["passed"] is False
        assert second_data["iteration"] == 2

    @pytest.mark.asyncio
    async def test_change_plan_files_are_scored_as_the_draft(self) -> None:
        evidence = [{"id": "docs/auth.md", "topic": "authentication"}]
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": f"  - ResearchAgent: {_json({'items': evidence})}"},
            {"text": "\nFrom update:"},
            {
                "text": (
                    "  - WriterAgent: "
                    + _json(
                        {
                            "files": [
                                {
                                    "path": "docs/auth.md",
                                    "content": "Authentication docs/auth.md " * 80,
                                }
                            ]
                        }
                    )
                )
            },
        ]

        result = await EvaluatorNode().invoke_async(blocks)
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert payload["passed"] is True
        assert payload["score"] >= 0.7

    @pytest.mark.asyncio
    async def test_evidence_bundle_items_are_scored(self) -> None:
        evidence = [{"id": "docs/auth.md", "topic": "authentication"}]
        result = await EvaluatorNode().invoke_async(
            _blocks(
                {"items": evidence},
                "Authentication docs/auth.md " * 80,
            )
        )
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert payload["score"] >= 0.7

    @pytest.mark.asyncio
    async def test_draft_from_answer_node(self) -> None:
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = "Answer text neon doc-1 " * 30
        node = EvaluatorNode()
        result = await node.invoke_async(_blocks(evidence, draft, source="answer"))
        node_result = result.results["evaluate"].result
        assert isinstance(node_result, AgentResult)
        payload = json.loads(node_result.message["content"][0]["text"])
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
        node_result = result.results["evaluate"].result
        assert isinstance(node_result, AgentResult)
        payload = json.loads(node_result.message["content"][0]["text"])
        assert payload["passed"] is False
        assert payload["score"] < 0.7

    @pytest.mark.asyncio
    async def test_parse_node_input_shares_format(self) -> None:
        blocks = _blocks([{"id": "doc-1"}], "draft text")
        deps = parse_node_input(blocks)
        assert "research" in deps
        assert "update" in deps
        assert deps["update"]["draft"] == "draft text"
