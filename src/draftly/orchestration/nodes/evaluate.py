"""Deterministic quality gate: grounding, completeness, source coverage."""

from __future__ import annotations

import re
from typing import Any

import structlog
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

from draftly.orchestration.nodes.base import agent_result, parse_node_input

logger = structlog.get_logger(__name__)

_EXT_RE = re.compile(
    r"\.(md|rst|adoc|py|ts|js|tsx|jsx|go|java|rb|rs|yaml|yml|toml|json|sh)$",
    re.IGNORECASE,
)
# Evidence ids often carry source locators ("src/authly/oauth.py:39-75");
# the draft can never echo the line range, so it must not block citation.
_LINE_REF_RE = re.compile(r":\d+(?:-\d+)?$")

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

    parts: list[str] = []
    files = payload.get("files")
    if isinstance(files, list):
        for file in files:
            if not isinstance(file, dict):
                continue
            path = str(file.get("path") or "")
            body = str(file.get("content") or "")
            if path or body:
                parts.append(f"{path}\n{body}")

    # Plans cite source ids in their prose (summary/commit_message/rationale),
    # rarely in the doc bodies themselves — score that prose too or
    # citation coverage can only ever be zero on good plans.
    prose: list[str] = []
    for field in ("summary", "commit_message", "rationale"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            prose.append(value)
    for group in ("evidence", "sources"):
        for item in payload.get(group) or []:
            if isinstance(item, str) and item.strip():
                prose.append(item)
    parts.extend(prose)
    return "\n\n".join(parts)


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
    normalized: list[dict] = []
    for item in evidence:
        if isinstance(item, dict):
            normalized.append(item)
        elif hasattr(item, "model_dump"):
            normalized.append(item.model_dump())
    return normalized


def _evidence_match_tokens(entry: dict) -> set[str]:
    """Normalized substrings that indicate the draft references ``entry``.

    Accepts the full evidence id/path (with or without a trailing extension,
    and with any ``:line`` source locator stripped) and the path's terminal
    basename, so a draft that links the source doc by its file name — or
    documents a feature whose code locator is ``oauth.py`` while the prose
    says "OAuth" — is recognized as citing it. Matching is case-insensitive
    (the generated markdown capitalizes feature names the code path never
    does).
    """
    tokens: set[str] = set()
    for field in ("id", "url"):
        raw = (entry.get(field) or "").strip()
        if not raw:
            continue
        raw = _LINE_REF_RE.sub("", raw)
        no_ext = _EXT_RE.sub("", raw).rstrip("/")
        no_ext_l = no_ext.lower()
        tokens.add(no_ext_l)
        base = no_ext.rsplit("/", 1)[-1].lower()
        if base and base in _COMMON_BASENAMES:
            continue
        if base and len(base) >= 3:
            tokens.add(base)
    return {t for t in tokens if len(t) >= 3}


def _evidence_topic(entry: dict) -> str:
    """Coverage topic for an evidence item.

    Prefers the item's ``topic`` field; falls back to the terminal basename of
    its id/url (the feature the doc must cover) because live EvidenceBundle
    items frequently omit ``topic``.
    """
    topic = entry.get("topic")
    if isinstance(topic, str) and topic.strip():
        return topic.strip().lower()
    for field in ("id", "url"):
        raw = str(entry.get(field) or "").strip()
        if not raw:
            continue
        raw = _LINE_REF_RE.sub("", raw)
        base = _EXT_RE.sub("", raw).rstrip("/").rsplit("/", 1)[-1].lower()
        if base and base not in _COMMON_BASENAMES and len(base) >= 3:
            return base
    return ""


def _evidence_has_signals(entry: dict) -> bool:
    """True if the item carries any field the gate can match against."""
    return any(
        bool((entry.get(field) or "").strip())
        for field in ("id", "url", "topic")
    )


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
    # Case-insensitive: generated markdown capitalizes feature names the
    # code-locator evidence ids never do.
    draft_lower = draft.lower()
    cited = sum(
        1
        for e in evidence
        if any(t in draft_lower for t in _evidence_match_tokens(e))
    )
    coverage = cited / max(len(evidence), 1)
    score += coverage * 0.4
    if coverage > 0.8:
        reasons.append(f"Grounded in {cited}/{len(evidence)} sources")

    # Completeness: does the draft cover the key topics?
    topics = [t for t in (_evidence_topic(e) for e in evidence) if t]
    covered = sum(1 for t in topics if t in draft_lower)
    completeness = covered / max(len(topics), 1)
    score += completeness * 0.3
    if completeness > 0.7:
        reasons.append(f"Covers {covered}/{len(topics)} key topics")
    elif topics:
        # Failure-gated feedback: name the missing topics so the writer knows
        # what the revision must add. Without this the only signal on a failed
        # completeness check is the opaque score and the loop flails.
        missing = [t for t in topics if t not in draft_lower]
        reasons.append(f"Missing topics: {', '.join(missing)}")

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
        *,
        rubric_grader: Any,
        drafts_repo: Any = None,
    ) -> None:
        if rubric_grader is None:
            raise TypeError(
                "EvaluatorNode requires a rubric_grader; the LLM grader is "
                "mandatory production wiring (deterministic gate stays the "
                "pass/fail signal, the grader enriches reasons on failures)."
            )
        self.name = name
        self.iteration = 0
        self.max_iterations = max_iterations
        self.rubric_grader = rubric_grader
        #: When set, the scored draft is assembled from the latest sealed
        #: generation of the draft store instead of inline ``files[].content``
        #: (whose bytes never enter tool-output JSON anymore). None keeps the
        #: legacy inline path so offline fixtures stay repo-free.
        self.drafts_repo = drafts_repo

    async def _store_draft(self, run_id: str | None) -> tuple[str, bool]:
        """Assembled content of the latest sealed generation for ``run_id``.

        Returns ``(content, has_files)``; ``has_files`` is True when at least
        one sealed revision exists. Content joins revisions in path order.
        Store failures degrade to empty (the waiver paths below still gate on
        evidence, not on the draft text).
        """
        if self.drafts_repo is None or not run_id:
            return "", False
        try:
            revisions = await self.drafts_repo.get_latest(run_id)
        except Exception:
            logger.warning("drafts_get_latest_failed", run_id=run_id, exc_info=True)
            return "", False
        if not revisions:
            return "", False
        parts = [f"{rev.path}\n{rev.content}" for rev in revisions]
        return "\n\n".join(parts), True

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
        files_present = False
        has_drafts = False
        if self.drafts_repo is not None:
            store_draft, has_drafts = await self._store_draft(
                (invocation_state or {}).get("run_id")
            )
            files_present = has_drafts
            # Plans carry only metadata now; their prose (summary/commit
            # message) still cite source ids, so score it on top of the
            # assembled store content.
            for dep_id in ("answer", "update", "create"):
                if dep_id in deps:
                    draft = _draft_text(deps[dep_id])
            if has_drafts:
                draft = f"{store_draft}\n\n{draft}" if draft.strip() else store_draft
        else:
            for dep_id in ("answer", "update", "create"):
                if dep_id in deps:
                    payload = deps[dep_id]
                    if isinstance(payload, dict) and payload.get("files"):
                        files_present = True
                    draft = _draft_text(payload)

        evidence = []
        for evidence_source in ("context", "research"):
            evidence = _research_evidence(deps.get(evidence_source))
            if evidence:
                break

        score, reasons = compute_quality(evidence, draft)
        passed = score >= 0.7
        waived = False
        escalated = False

        if not passed and files_present and not evidence:
            # With zero usable evidence the coverage/completeness signals are
            # uncomputable; failing forever only burns the budget in revision
            # loops before the ReviewGate can run. A file-scoped plan proceeds
            # to the deterministic changelog + human review gates instead.
            passed = True
            waived = True
            if not any("No structured evidence" in r for r in reasons):
                reasons.append(
                    "No structured evidence available; waived coverage on file-scoped plan"
                )
        elif (
            not passed
            and files_present
            and evidence
            and not any(_evidence_has_signals(e) for e in evidence)
        ):
            # Evidence exists but carries no id/url/topic (excerpt-only
            # items); coverage and completeness are uncomputable and the
            # loop would just burn the budget. Same waiver as the empty
            # case: proceed to the changelog + human review gates.
            passed = True
            waived = True
            if not any("No usable evidence" in r for r in reasons):
                reasons.append(
                    "Evidence carries no usable id/url/topic; waived coverage on file-scoped plan"
                )

        if not reasons:
            reasons.append(f"Score {score:.2f} (threshold: 0.70)")

        # Mandatory rubric grader: an LLM judge enriches reasons on a failed
        # draft with specific guidance (e.g. naming the exact missing topics)
        # that the deterministic gate cannot produce. Runs whenever the gate
        # fails and is always wired (see GraphBuilder call sites). Exceptions
        # degrade to the deterministic verdict rather than crashing the run.
        if not passed:
            try:
                grade = await self.rubric_grader.grade(draft=draft, evidence=evidence)
                for reason in grade.reasons:
                    if reason and reason not in reasons:
                        reasons.append(f"[rubric] {reason}")
            except Exception:
                logger.warning("rubric_grader_failed", exc_info=True)

        if not passed and self.iteration >= self.max_iterations:
            # Failed evaluation exhausted its revision budget. Escalate to the
            # human ReviewGate (mirror of the waiver path) so the graph routes
            # toward deliver instead of burning max_node_executions looping on
            # an impossible gate. The prompts' failure policy requires this.
            passed = True
            escalated = True
            reasons.append(
                f"Quality threshold not met after {self.iteration} evaluations; "
                "escalated to human review"
            )

        logger.info(
            "evaluate_verdict",
            run_id=(invocation_state or {}).get("run_id"),
            passed=passed,
            score=score,
            waived=waived,
            escalated=escalated,
            evidence_count=len(evidence),
            files_present=files_present,
            has_drafts=has_drafts,
            draft_chars=len(draft),
            reasons=reasons,
        )

        result = {
            "passed": passed,
            "score": score,
            "reasons": reasons,
            "iteration": self.iteration,
            "escalated": escalated,
        }
        if self.drafts_repo is not None:
            # Delivery gate reads this (delivery_content_ready). Additive only:
            # legacy fixtures (drafts_repo=None) keep the exact key set.
            result["has_drafts"] = has_drafts

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(result)
                )
            },
        )
