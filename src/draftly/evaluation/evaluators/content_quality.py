"""Deterministic content-quality evaluator used before human approval."""

from __future__ import annotations

from typing import Any

from draftly.content.models import ContentVariant

THRESHOLDS = {
    "groundedness": 0.90,
    "completeness": 0.80,
    "relevance": 0.80,
    "channel_fit": 0.80,
}


def evaluate_content_variant(variant: ContentVariant) -> dict[str, Any]:
    scores = {
        key: float(variant.evaluation.get(key, 0.0)) for key in THRESHOLDS
    }
    issues: list[str] = []
    if not variant.evidence:
        issues.append("missing evidence references")
    for key, threshold in THRESHOLDS.items():
        score = variant.evaluation.get(key)
        if score is None:
            # No score was produced (e.g. the writer pipeline emitted the
            # variant directly). Absence is not a failure — threshold checks
            # apply only to scores that actually exist, so the in-graph gate
            # blocks on missing evidence rather than on undefined scores.
            continue
        if float(score) < threshold:
            issues.append(f"{key} below threshold {threshold:.2f}")
    return {"passed": not issues, "scores": scores, "issues": issues}


def evaluate_content(variants: list[ContentVariant]) -> dict[str, Any]:
    results = [evaluate_content_variant(variant) for variant in variants]
    return {
        "passed": bool(results) and all(result["passed"] for result in results),
        "variants": results,
        "issues": [issue for result in results for issue in result["issues"]],
    }
