"""Content evaluation gate: ContentEvaluationNode output must drive
``eval_passed`` so the content graph can block persistence/delivery when a
variant is unsupported (no evidence).

The in-graph evaluator is deterministic: it judges grounding from the
variant's evidence (presence => pass, absence => blocking "missing evidence
references" issue) and only applies the threshold checks when an explicit
scores dict is present. Score keys that are absent (e.g. ``evaluation={}``
from the writer pipeline) are NOT treated as below-threshold failures.
"""

from __future__ import annotations

import json

from strands.multiagent.base import MultiAgentResult, NodeResult, Status
from strands.multiagent.graph import GraphState

from draftly.integrations.strands import graph  # noqa: F401  (import-order guard)
from draftly.orchestration.graphs.content_graph import ContentEvaluationNode
from draftly.orchestration.routing.conditions import eval_passed, needs_revision


def _task(blocks: list[dict]) -> list[dict]:
    return blocks


def _blocks(variants: dict[str, dict]) -> list[dict]:
    blocks = [
        {"text": "Original Task: {}"},
        {"text": "\nInputs from previous nodes:"},
    ]
    for node_id, payload in variants.items():
        blocks.append({"text": f"\nFrom {node_id}:"})
        blocks.append({"text": f"  - Agent: {json.dumps(payload)}"})
    return blocks


def _evaluate_output(result: MultiAgentResult) -> dict:
    node = result.results["evaluate"]
    content = (node.result.message or {}).get("content", [])
    for block in content:
        if isinstance(block, dict) and "text" in block:
            return json.loads(block["text"])
    raise AssertionError("evaluate node has no text payload")


def _grounded(channel: str) -> dict:
    return {
        "title": f"{channel} release",
        "body": f"A grounded {channel} update.",
        "evidence": [{"source_id": "doc-1"}],
    }


async def test_grounded_variants_pass_and_gate_opens() -> None:
    node = ContentEvaluationNode()
    result = await node.invoke_async(
        _task(_blocks({
            "content_blog": _grounded("blog"),
            "content_linkedin": _grounded("linkedin"),
            "content_x": _grounded("x"),
        }))
    )

    output = _evaluate_output(result)
    assert output["passed"] is True
    assert output["issues"] == []

    state = GraphState()
    state.results["evaluate"] = NodeResult(result=result, status=Status.COMPLETED)
    assert eval_passed(state)
    assert not needs_revision(state)


async def test_unsupported_variant_blocks_and_gate_closes() -> None:
    node = ContentEvaluationNode()
    result = await node.invoke_async(
        _task(_blocks({
            "content_blog": _grounded("blog"),
            "content_linkedin": {
                "title": "telemetry",
                "body": "claims telemetry behavior",
                "evidence": [],
            },
            "content_x": _grounded("x"),
        }))
    )

    output = _evaluate_output(result)
    assert output["passed"] is False
    assert "missing evidence references" in output["issues"]

    state = GraphState()
    state.results["evaluate"] = NodeResult(result=result, status=Status.COMPLETED)
    assert not eval_passed(state)
    assert needs_revision(state)