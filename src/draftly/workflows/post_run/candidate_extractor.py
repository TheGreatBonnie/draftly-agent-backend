"""Post-run memory extraction (spec 2026-08-23 §Components 3).

Deterministic packaging only — no LLM calls here; judgment is deferred to
the async Memory Curator. Extraction must never raise into the runner.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from draftly.memory.candidates.models import MemoryCandidate

logger = structlog.get_logger(__name__)


def _result_attr(result: Any, name: str, default: Any) -> Any:
    value = getattr(result, name, default)
    return default if value is default else value


def _node_payload(result: Any, node_id: str) -> dict[str, Any]:
    """Extract a structured payload from a Strands graph result node."""
    for node in getattr(result, "execution_order", None) or []:
        if getattr(node, "node_id", None) != node_id:
            continue
        node_result = getattr(node, "result", None)
        nested = getattr(node_result, "result", None)
        results = getattr(nested, "results", None)
        if isinstance(results, dict) and node_id in results:
            node_result = getattr(results[node_id], "result", None)
        else:
            node_result = nested or node_result

        structured = getattr(node_result, "structured_output", None)
        if isinstance(structured, dict):
            return structured
        dump = getattr(structured, "model_dump", None)
        if callable(dump):
            value = dump()
            if isinstance(value, dict):
                return value

        message = getattr(node_result, "message", None)
        content = message.get("content", []) if isinstance(message, dict) else []
        for block in content:
            if not isinstance(block, dict) or "text" not in block:
                continue
            try:
                value = json.loads(block["text"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
    return {}


def _graph_files(result: Any) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for node_id in ("update", "create"):
        payload = _node_payload(result, node_id)
        for file in payload.get("files", []) or []:
            if isinstance(file, dict) and file.get("path"):
                files.append(file)
    return files


def _graph_tools(result: Any) -> list[str]:
    """Collect tool names from agent message blocks in execution order."""
    names: list[str] = []
    for node in getattr(result, "execution_order", None) or []:
        node_result = getattr(node, "result", None)
        values = [node_result, getattr(node_result, "result", None)]
        nested = values[-1]
        nested_results = getattr(nested, "results", None)
        if isinstance(nested_results, dict):
            values.extend(
                getattr(value, "result", None) for value in nested_results.values()
            )
        for value in values:
            message = getattr(value, "message", None)
            content = message.get("content", []) if isinstance(message, dict) else []
            for block in content:
                if not isinstance(block, dict):
                    continue
                tool_use = block.get("toolUse")
                name = tool_use.get("name") if isinstance(tool_use, dict) else None
                if isinstance(name, str) and name and name not in names:
                    names.append(name)
    return names


def extract_candidates(state: Any, surface: str) -> list[MemoryCandidate]:
    """Package run outputs into structured memory candidates."""
    result = getattr(state, "result", None)
    if result is None:
        return []
    org_id = str((state.event or {}).get("project_id") or "") or None
    out: list[MemoryCandidate] = []

    changed_files = _result_attr(result, "changed_files", []) or []
    if not changed_files:
        changed_files = (state.event or {}).get("pull_request", {}).get("changed_files", [])
    changed_files = [
        item.get("path") if isinstance(item, dict) else str(item)
        for item in changed_files
    ]
    docs_touched = _result_attr(result, "docs_touched", []) or []
    if not docs_touched:
        docs_touched = [
            str(file["path"])
            for file in _graph_files(result)
            if str(file.get("path", "")).lower().endswith((".md", ".rst", ".adoc"))
        ]
    for src, dst in zip(changed_files, docs_touched):
        out.append(
            MemoryCandidate(
                org_id=org_id,
                candidate_type="doc_relation",
                payload={
                    "source_key": src,
                    "target_key": dst,
                    "relation_type": "DOCUMENTED_BY",
                },
                source_type=surface,
                source_id=str(getattr(state, "run_id", "") or ""),
                evidence=[src],
                confidence=0.7,
            )
        )

    for fact in _result_attr(result, "detected_facts", []) or []:
        content = str(fact.get("content") or "").strip()
        if not content:
            continue
        out.append(
            MemoryCandidate(
                org_id=org_id,
                candidate_type="fact",
                payload={"content": content},
                source_type=surface,
                source_id=str(getattr(state, "run_id", "") or ""),
                evidence=[str(e) for e in fact.get("evidence", [])],
                confidence=float(fact.get("confidence", 0.5)),
            )
        )
    return out


async def record_post_run_memory(context: Any, state: Any, surface: str) -> None:
    """Episode + candidate capture after a completed run. Never raises into
    the runner — callers wrap this in fail-open handling as well."""
    event = state.event or {}
    org_id = str(event.get("project_id") or "") or None
    result = getattr(state, "result", None)
    actions = [
        str(getattr(node, "node_id", ""))
        for node in getattr(result, "execution_order", None) or []
        if getattr(node, "node_id", None)
    ]
    evaluation = _node_payload(result, "evaluate")
    artifacts = [str(file["path"]) for file in _graph_files(result)]
    tools_used = _graph_tools(result)
    delivery = _node_payload(result, "deliver")
    if delivery.get("reference"):
        artifacts.append(str(delivery["reference"]))

    if getattr(context, "episodic", None):
        await context.episodic.record_episode(
            org_id=org_id,
            agent_run_id=str(getattr(state, "run_id", "") or "") or None,
            trigger_type=surface,
            trigger_id=str(event.get("event_id") or ""),
            trigger_summary=str(event.get("title") or surface),
            actions_taken=actions,
            tools_used=tools_used,
            outcome="success",
            evaluation_results=evaluation or None,
            artifacts_created=artifacts,
        )

    if getattr(context, "candidates", None):
        for candidate in extract_candidates(state, surface):
            await context.candidates.enqueue(candidate)
