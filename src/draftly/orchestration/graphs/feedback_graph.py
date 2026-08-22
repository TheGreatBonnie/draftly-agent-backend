"""Feedback loop graph: support questions → documentation gaps.

    summarize_clusters → detect_gaps → prioritize ─(has_gaps)─► enqueue

All nodes are deterministic ``MultiAgentBase`` custom nodes (no model
keys): clustering by topic, threshold-based gap detection, frequency×
severity prioritization, and emission of gap records the runner persists /
turns into documentation runs.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from strands.multiagent import GraphBuilder
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)
from strands.session.session_manager import SessionManager

from draftly.orchestration.nodes.base import agent_result, parse_node_input

FEEDBACK_GRAPH_ID = "draftly-feedback-graph"
DEFAULT_GAP_THRESHOLD = 2


def _task_payload(task: Any) -> dict:
    """Read the graph task as a dict (raw JSON string or parsed deps).

    Entry nodes receive ``[ContentBlock(text=<raw task>)]`` from
    ``_build_node_input``; downstream nodes receive the formatted
    dependency blocks parsed by :func:`parse_node_input`.
    """
    if isinstance(task, str):
        try:
            data = json.loads(task)
            return data if isinstance(data, dict) else {"questions": []}
        except json.JSONDecodeError:
            return {"questions": []}

    if isinstance(task, list):
        for block in task:
            text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, TypeError):
                continue

    deps = parse_node_input(task)
    return next(iter(deps.values()), {"questions": []})


class SummarizeClustersNode(MultiAgentBase):
    """Group raw questions into topic clusters."""

    def __init__(self, name: str = "summarize") -> None:
        self.name = name

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        payload = _task_payload(task)
        questions = payload.get("questions", [])

        grouped: dict[str, list[dict]] = defaultdict(list)
        for q in questions:
            topic = str(q.get("topic", "") or "general").strip().lower()
            grouped[topic].append(q)

        clusters = [
            {
                "topic": topic,
                "count": len(items),
                "sample_question": items[0].get("question", ""),
                "sources": sorted({str(i.get("source", "")) for i in items}),
            }
            for topic, items in sorted(grouped.items())
        ]

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: NodeResult(result=agent_result({"clusters": clusters}))},
        )


class DetectGapsNode(MultiAgentBase):
    """Clusters at or above the question threshold are doc gaps."""

    def __init__(
        self,
        name: str = "detect_gaps",
        threshold: int = DEFAULT_GAP_THRESHOLD,
    ) -> None:
        self.name = name
        self.threshold = threshold

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        clusters = deps.get("summarize", {}).get("clusters", [])
        gaps = [c for c in clusters if c.get("count", 0) >= self.threshold]

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "gaps": gaps,
                            "threshold": self.threshold,
                            "gap_count": len(gaps),
                        }
                    )
                )
            },
        )


class PrioritizeGapsNode(MultiAgentBase):
    """Rank gaps by question frequency (descending)."""

    def __init__(self, name: str = "prioritize") -> None:
        self.name = name

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        gaps = deps.get("detect_gaps", {}).get("gaps", [])
        ranked = sorted(gaps, key=lambda g: g.get("count", 0), reverse=True)

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "prioritized_gaps": ranked,
                            "total": len(ranked),
                        }
                    )
                )
            },
        )


class EnqueueGapsNode(MultiAgentBase):
    """Emit documentation-run requests for each prioritized gap."""

    def __init__(self, name: str = "enqueue") -> None:
        self.name = name

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        gaps = deps.get("prioritize", {}).get("prioritized_gaps", [])
        enqueued = [
            {
                "gap_id": f"gap-{i + 1:03d}",
                "topic": g.get("topic", ""),
                "count": g.get("count", 0),
                "action": "create",
            }
            for i, g in enumerate(gaps)
        ]

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "enqueued": enqueued,
                            "enqueued_count": len(enqueued),
                        }
                    )
                )
            },
        )


def has_gaps(state: Any) -> bool:
    """Edge condition: prioritize found at least one gap."""
    from draftly.orchestration.nodes.base import node_data

    if "prioritize" not in state.results:
        return False
    return node_data(state, "prioritize").get("total", 0) > 0


def build_feedback_graph(
    session_manager: SessionManager | None = None,
    tools_registry: Any = None,
    model: Any = None,
    hooks: list[Any] | None = None,
    *,
    graph_id: str = FEEDBACK_GRAPH_ID,
    gap_threshold: int = DEFAULT_GAP_THRESHOLD,
):
    """Build the scheduled feedback-loop graph."""
    builder = GraphBuilder()
    builder.set_graph_id(graph_id)

    builder.add_node(SummarizeClustersNode(), "summarize")
    builder.set_entry_point("summarize")

    builder.add_node(DetectGapsNode(threshold=gap_threshold), "detect_gaps")
    builder.add_edge("summarize", "detect_gaps")

    builder.add_node(PrioritizeGapsNode(), "prioritize")
    builder.add_edge("detect_gaps", "prioritize")

    builder.add_node(EnqueueGapsNode(), "enqueue")
    builder.add_edge("prioritize", "enqueue", condition=has_gaps)

    builder.set_max_node_executions(8)
    builder.set_execution_timeout(300.0)
    builder.set_node_timeout(60.0)

    if session_manager is not None:
        builder.set_session_manager(session_manager)
    providers: list[Any] = list(hooks or [])
    if providers:
        builder.set_hook_providers(providers)

    return builder.build()
