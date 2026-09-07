"""Deterministic quality gate: grounding, completeness, source coverage."""

from __future__ import annotations

import re
from typing import Any

from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

from draftly.orchestration.nodes.base import agent_result, parse_node_input

_EXT_RE = re.compile(r"\.(md|rst|adoc)$", re.IGNORECASE)

# Basenames too generic to count as a citation on their own (they would
# match a draft even when the specific source doc was never referenced).
_COMMON_BASENAMES = {
    "readme",
    "index",
    "overview",
    "introduction",
    "getting-started",
    "api",
    "faq",
    "guide",
    "reference",
    "docs",
}


def _draft_text(payload: dict[str, Any]) -> str:
    """Flatten answer or documentation-plan output into scored text."""
    content = payload.get("content") or payload.get("draft")
    if isinstance(content, str):
        return content

    files = payload.get("files")
    if isinstance(files, list):
        chunks: list[str] = []
        for file in files:
            if not isinstance(file, dict):
                continue
            path = str(file.get("path") or "")
            body = str(file.get("content") or "")
            if path or body:
                chunks.append(f"{path}\n{body}")
        return "\n\n".join(chunks)
    return ""


def _research_evidence(payload: Any) -> list[dict]:
    """Normalize EvidenceBundle and legacy evaluator payload shapes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    evidence = payload.get("evidence")
    if evidence is None:
        evidence = payload.get("items", [])
    if not isinstance(evidence, list):
        return []
    return [item for item in evidence if isinstance(item, dict)]


def _evidence_match_tokens(entry: dict) -> set[str]:
    """Normalized substrings that indicate the draft references ``entry``.

    Accepts the full evidence id/path (with or without a trailing extension)
    and the path's terminal basename, so a draft that links the source doc by
    its file name is recognized as citing it — not just an exact whole-id
    byte match the writer can never produce.
    """
    tokens: set[str] = set()
    for field in ("id", "url"):
        raw = (entry.get(field) or "").strip()
        if not raw:
            continue
        no_ext = _EXT_RE.sub("", raw).rstrip("/")
        tokens.add(no_ext)
        base = no_ext.rsplit("/", 1)[-1]
        if base and base.lower() in _COMMON_BASENAMES:
            continue
        if base and len(base) >= 3:
            tokens.add(base)
    return {t for t in tokens if len(t) >= 3}


def compute_quality(
    evidence: list[dict],
    draft: str,
) -> tuple[float, list[str]]:
    """
    Deterministic quality scoring: citation coverage, completeness, grounding.
    """

    reasons: list[str] = []
    score = 0.0

    # Citation coverage: does the draft reference available evidence?
    cited = sum(
        1 for e in evidence if any(t in draft for t in _evidence_match_tokens(e))
    )
    coverage = cited / max(len(evidence), 1)
    score += coverage * 0.4
    if coverage > 0.8:
        reasons.append(f"Grounded in {cited}/{len(evidence)} sources")

    # Completeness: does the draft cover the key topics?
    topics = [e.get("topic", "") for e in evidence if e.get("topic")]
    covered = sum(1 for t in topics if t.lower() in draft.lower())
    completeness = covered / max(len(topics), 1)
    score += completeness * 0.3
    if completeness > 0.7:
        reasons.append(f"Covers {covered}/{len(topics)} key topics")

    # Length heuristic: very short drafts are usually incomplete
    length_score = min(len(draft) / 500, 1.0)
    score += length_score * 0.3
    if length_score > 0.5:
        reasons.append("Adequate detail level")

    return score, reasons


class EvaluatorNode(MultiAgentBase):
    """Deterministic quality gate for generated documentation output."""

    def __init__(
        self,
        name: str = "evaluate",
        max_iterations: int = 3,
    ) -> None:
        self.name = name
        self.iteration = 0
        self.max_iterations = max_iterations

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        self.iteration += 1

        # Parse dependency outputs from the graph's ContentBlock input.
        # The graph feeds prior node results as a list[ContentBlock] with
        # "From <dep_id>:" sections — the draft comes from whichever of
        # answer/update/create ran; evidence comes from research.
        # NOTE: the research swarm's final message may be plain text, not
        # JSON; parse_node_input skips non-JSON payloads, so evidence safely
        # degrades to [] in that case.
        deps = parse_node_input(task)

        draft = ""
        for dep_id in ("answer", "update", "create"):
            if dep_id in deps:
                draft = _draft_text(deps[dep_id])

        evidence = []
        for evidence_source in ("context", "research"):
            evidence = _research_evidence(deps.get(evidence_source))
            if evidence:
                break

        score, reasons = compute_quality(evidence, draft)
        passed = score >= 0.7

        if not reasons:
            reasons.append(f"Score {score:.2f} (threshold: 0.70)")
        if not passed and self.iteration >= self.max_iterations:
            reasons.append(f"Quality threshold not met after {self.iteration} evaluations")

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "passed": passed,
                            "score": score,
                            "reasons": reasons,
                            "iteration": self.iteration,
                        }
                    )
                )
            },
        )
