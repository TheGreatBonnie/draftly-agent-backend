"""Tool-usage trajectory extraction from Strands graph results.

Real agent runs are evaluated by what tools each node actually invoked.
This module walks a completed ``GraphResult`` and flattens the metrics of
every agent that executed into per-node and aggregate tool trajectories.
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.multiagent.base import NodeResult

logger = structlog.get_logger(__name__)

ToolCall = dict[str, Any]


def node_agent_results(node: Any) -> list[Any]:
    """Return the AgentResults of a node whether it is a ``NodeResult`` or a ``GraphNode``.

    ``GraphResult.execution_order`` holds ``GraphNode`` objects, which are plain
    dataclasses carrying their result on ``.result`` (a ``NodeResult``) — they do
    NOT expose ``get_agent_results()`` directly. Resolve through the ``NodeResult``
    when present; fall back to the node itself for objects that already implement
    ``get_agent_results()`` (e.g. test stubs or nested ``NodeResult``).
    """
    if hasattr(node, "get_agent_results"):
        return list(node.get_agent_results())
    node_result = getattr(node, "result", None)
    if node_result is not None and hasattr(node_result, "get_agent_results"):
        return list(node_result.get_agent_results())
    return []


def _agent_tool_metrics(agent_result: Any) -> list[ToolCall]:
    """Extract tool-usage records from a single AgentResult's metrics.

    ``EventLoopMetrics.tool_metrics`` is keyed by tool name, so the first
    call in a node (``metrics.tool_metrics.items()`` preserves insertion
    order) defines the input snapshot for that tool.
    """
    metrics = getattr(agent_result, "metrics", None)
    if metrics is None:
        return []
    tool_metrics = getattr(metrics, "tool_metrics", None) or {}
    records: list[ToolCall] = []
    for name, tm in tool_metrics.items():
        input_snapshot = getattr(tm.tool, "input", {}) if getattr(tm, "tool", None) else {}
        records.append(
            {
                "name": name,
                "input": input_snapshot,
                "call_count": getattr(tm, "call_count", 0),
                "success_count": getattr(tm, "success_count", 0),
                "error_count": getattr(tm, "error_count", 0),
            }
        )
    return records


def node_tool_usage(node: NodeResult) -> list[ToolCall]:
    """Tool usage across every child agent of a node, in execution order."""
    calls: list[ToolCall] = []
    for agent_result in node_agent_results(node):
        calls.extend(_agent_tool_metrics(agent_result))
    return calls


def extract_trajectories(graph_result: Any) -> dict[str, list[ToolCall]]:
    """Map node_id -> tool-usage records from a completed graph result.

    Preserves ``execution_order`` so tool calls align with the order nodes
    actually ran in.
    """
    trajectories: dict[str, list[ToolCall]] = {}
    for node in getattr(graph_result, "execution_order", []) or []:
        node_id = getattr(node, "node_id", None)
        if not node_id:
            continue
        calls = node_tool_usage(node)
        if calls:
            trajectories[node_id] = calls
    logger.debug(
        "trajectory_extracted",
        nodes=list(trajectories.keys()),
        total_calls=sum(len(v) for v in trajectories.values()),
    )
    return trajectories


def flatten_trajectory(trajectories: dict[str, list[ToolCall]]) -> list[str]:
    """Flatten per-node tool usage into an ordered list of tool names.

    A tool called in multiple nodes appears once per node it ran in.
    """
    names: list[str] = []
    for node_id in trajectories:
        names.extend(call["name"] for call in trajectories[node_id])
    return names
