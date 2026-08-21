"""Deterministic node helpers shared by graph nodes and conditions."""

from __future__ import annotations

import json
from typing import Any

from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentResult
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.content import ContentBlock, Message


def agent_result(data: dict) -> AgentResult:
    """
    Wrap structured data so downstream nodes/conditions can parse it.
    """

    return AgentResult(
        stop_reason="end_turn",
        message=Message(
            content=[ContentBlock(text=json.dumps(data))],
            role="assistant",
        ),
        metrics=EventLoopMetrics(),
        state=None,
    )


def node_data(state: Any, node_id: str) -> dict:
    """
    Read a node's structured payload from graph state.

    Three shapes (verified against strands/multiagent/graph.py):

    1. Custom MultiAgentBase nodes: the graph stores the returned
       MultiAgentResult at state.results[node_id].result; our AgentResult
       (JSON in message text) lives one level deeper, keyed by the node's
       own name.
    2. LLM Agent nodes with structured_output_model: the graph stores the
       AgentResult DIRECTLY at state.results[node_id].result; the parsed
       model is on ``.structured_output`` (NOT in message content).
    3. Restored-from-session LLM results: ``structured_output`` is lost and
       the message content carries toolUse/toolResult blocks — there is no
       parsable payload. Raises ValueError; callers in routing conditions
       should use :func:`safe_node_data`.
    """
    node_result = state.results[node_id].result

    if isinstance(node_result, MultiAgentResult):
        node_result = node_result.results[node_id].result

    structured = getattr(node_result, "structured_output", None)

    if structured is not None:
        return structured.model_dump()

    content = node_result.message.get("content", [])
    for block in content:
        if isinstance(block, dict) and "text" in block:
            return json.loads(block["text"])

    raise ValueError(
        f"node <{node_id}> has no parsable payload "
        "(structured output lost, message has no text block)"
    )


def safe_node_data(state: Any, node_id: str) -> dict | None:
    """Best-effort :func:`node_data`; returns None instead of raising.

    Session persistence re-evaluates edge conditions at any time, often
    against restored results whose structured payloads did not survive
    serialization. Conditions must degrade to False, never crash.
    """
    try:
        return node_data(state, node_id)
    except Exception:  # noqa: BLE001 - defensive by design
        return None


def parse_node_input(task: Any) -> dict[str, dict]:
    """
    Parse the graph's node input into per-dependency structured payloads.

    IMPORTANT (verified against strands/multiagent/graph.py
    ``_build_node_input``): the graph does NOT pass the raw task string to
    nodes with satisfied dependencies. It builds a ``list[ContentBlock]``
    formatted as::

        Original Task: <task>
        Inputs from previous nodes:
        From <dep_id>:
          - <agent_name>: <str(AgentResult)>

    ``str(AgentResult)`` is the message text — for custom nodes that is the
    JSON we wrapped via ``agent_result()``. So:

      - a node WITHOUT dependencies receives the raw task string,
      - a node WITH dependencies receives the ContentBlock list and must
        parse the "From <dep_id>:" sections.

    The per-agent line is ``  - <agent_name>: <json>`` (agent_name defaults
    to ``"Agent"``); JSON payloads directly on the line are also accepted.

    Returns {dep_id: parsed_json_dict} for every dependency present.
    """
    blocks = task if isinstance(task, list) else []
    text = "\n".join(
        b.get("text", "")
        if isinstance(b, dict)
        else getattr(b, "text", "")
        for b in blocks
    )

    result: dict[str, dict] = {}
    current_dep: str | None = None

    for line in text.splitlines():
        if line.startswith("From "):
            current_dep = line[len("From "):].strip().rstrip(":")
            continue

        if current_dep and line.startswith("  - "):
            payload = line[len("  - "):].strip()

            # Format is "<agent_name>: <json>" — take the part after the
            # first ": " when it looks like a JSON object/array.
            if ": " in payload:
                _, candidate = payload.split(": ", 1)
                if candidate.startswith(("{", "[")):
                    payload = candidate

            if payload.startswith(("{", "[")):
                try:
                    parsed = json.loads(payload)
                    result[current_dep] = parsed
                except json.JSONDecodeError:
                    pass

            current_dep = None

    return result
