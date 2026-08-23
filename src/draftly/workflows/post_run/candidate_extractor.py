"""Post-run memory extraction (spec 2026-08-23 §Components 3).

Deterministic packaging only — no LLM calls here; judgment is deferred to
the async Memory Curator. Extraction must never raise into the runner.
"""

from __future__ import annotations

from typing import Any

import structlog

from draftly.memory.candidates.models import MemoryCandidate

logger = structlog.get_logger(__name__)


def _result_attr(result: Any, name: str, default: Any) -> Any:
    value = getattr(result, name, default)
    return default if value is default else value


def extract_candidates(state: Any, surface: str) -> list[MemoryCandidate]:
    """Package run outputs into structured memory candidates."""
    result = getattr(state, "result", None)
    if result is None:
        return []
    org_id = str((state.event or {}).get("project_id") or "") or None
    out: list[MemoryCandidate] = []

    changed_files = _result_attr(result, "changed_files", []) or []
    docs_touched = _result_attr(result, "docs_touched", []) or []
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

    if getattr(context, "episodic", None):
        await context.episodic.record_episode(
            org_id=org_id,
            agent_run_id=None,
            trigger_type=surface,
            trigger_id=str(event.get("event_id") or ""),
            trigger_summary=str(event.get("title") or surface),
            actions_taken=[],
            tools_used=[],
            outcome="success",
            evaluation_results=None,
            artifacts_created=[],
        )

    if getattr(context, "candidates", None):
        for candidate in extract_candidates(state, surface):
            await context.candidates.enqueue(candidate)
