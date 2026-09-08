"""In-graph grounding judge: ``ContentEvaluationNode`` consults an LLM
judge per variant so fabricated-but-evidenced claims (e.g. a strategist
attaching irrelevant repo files) are blocked before persistence/delivery.

The judge is advisory and fail-open: when no judge is configured, or the
judge call errors, the deterministic gate alone decides.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent  # noqa: E402
from strands.multiagent.base import MultiAgentResult, NodeResult, Status  # noqa: E402
from strands.multiagent.graph import GraphState  # noqa: E402

from draftly.agents.content.judge import build_content_grounding_judge  # noqa: E402
from draftly.agents.content.schemas import ContentJudgeVerdict  # noqa: E402
from draftly.integrations.strands import graph  # noqa: F401, E402  (import-order guard)
from draftly.orchestration.graphs.content_graph import ContentEvaluationNode  # noqa: E402
from draftly.orchestration.routing.conditions import eval_passed, needs_revision  # noqa: E402
from tests.stub_model import StubModel  # noqa: E402


def _task(blocks: list[dict]) -> list[dict]:
    return blocks


def _event_blocks(event: dict, variants: dict[str, dict]) -> list[dict]:
    blocks = [
        {"text": f"Original Task: {json.dumps(event)}"},
        {"text": "\nInputs from previous nodes:"},
    ]
    for node_id, payload in variants.items():
        blocks.append({"text": f"\nFrom {node_id}:"})
        blocks.append({"text": f"  - Agent: {json.dumps(payload)}"})
    return blocks


def _grounded_variants() -> dict[str, dict]:
    return {
        "content_blog": {
            "title": "blog release",
            "body": "A grounded blog update.",
            "evidence": [{"source_id": "doc-1"}],
        },
        "content_linkedin": {
            "title": "linkedin release",
            "body": "A grounded linkedin update.",
            "evidence": [{"source_id": "doc-1"}],
        },
        "content_x": {
            "title": "x release",
            "body": "A grounded x update.",
            "evidence": [{"source_id": "doc-1"}],
        },
    }


def _event(source_evidence_content: list[dict[str, str]]) -> dict:
    return {"release": {"source_evidence_content": source_evidence_content}}


def _evaluate_output(result: MultiAgentResult) -> dict:
    node = result.results["evaluate"]
    content = (node.result.message or {}).get("content", [])
    for block in content:
        if isinstance(block, dict) and "text" in block:
            return json.loads(block["text"])
    raise AssertionError("evaluate node has no text payload")


# --- judge agent factory ---------------------------------------------------


def test_content_grounding_judge_uses_structured_output() -> None:
    agent = build_content_grounding_judge(StubModel())

    assert isinstance(agent, Agent)
    assert agent.name == "content_grounding_judge"
    assert agent._default_structured_output_model is ContentJudgeVerdict


# --- judge integration in ContentEvaluationNode ---------------------------


async def test_judge_passes_grounded_variants() -> None:
    evidence_content = [{"path": "docs/release.md", "content": "v2.0.0 grounding"}]
    seen: list[list[dict[str, str]]] = []

    async def judge(variant, evidence_content):
        seen.append(list(evidence_content))
        return ContentJudgeVerdict(grounded=True)

    node = ContentEvaluationNode(judge=judge)
    result = await node.invoke_async(
        _task(_event_blocks(_event(evidence_content), _grounded_variants()))
    )

    output = _evaluate_output(result)
    assert output["passed"] is True
    assert output["issues"] == []
    assert len(seen) == 3
    assert seen[0] == evidence_content


async def test_judge_blocks_fabricated_claims_and_gate_closes() -> None:
    evidence_content = [{"path": "docs/release.md", "content": "v2.0.0 grounding"}]

    async def judge(variant, evidence_content):
        return ContentJudgeVerdict(
            grounded=False,
            blocking_issues=["claims telemetry exporter behavior absent from evidence"],
        )

    node = ContentEvaluationNode(judge=judge)
    result = await node.invoke_async(
        _task(_event_blocks(_event(evidence_content), _grounded_variants()))
    )

    output = _evaluate_output(result)
    assert output["passed"] is False
    assert "claims telemetry exporter behavior absent from evidence" in output["issues"]

    state = GraphState()
    state.results["evaluate"] = NodeResult(result=result, status=Status.COMPLETED)
    assert not eval_passed(state)
    assert needs_revision(state)


async def test_judge_skipped_for_variants_that_already_fail() -> None:
    called: list[str] = []

    async def judge(variant, evidence_content):
        called.append(variant.channel.value)
        return ContentJudgeVerdict(grounded=True)

    node = ContentEvaluationNode(judge=judge)
    variants = _grounded_variants()
    variants["content_linkedin"]["evidence"] = []
    result = await node.invoke_async(_task(_event_blocks(_event([]), variants)))

    output = _evaluate_output(result)
    assert output["passed"] is False
    assert "missing evidence references" in output["issues"]
    # The empty-evidence variant never reaches the judge.
    assert called == ["blog", "x"]


async def test_judge_exception_fails_open() -> None:
    async def judge(variant, evidence_content):
        raise RuntimeError("model unavailable")

    node = ContentEvaluationNode(judge=judge)
    result = await node.invoke_async(
        _task(_event_blocks(_event([{"path": "docs/release.md", "content": "v2.0.0"}]), _grounded_variants()))
    )

    output = _evaluate_output(result)
    assert output["passed"] is True
    assert output["issues"] == []