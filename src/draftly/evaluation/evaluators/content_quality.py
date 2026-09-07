"""Deterministic content-quality evaluator used before human approval."""

from __future__ import annotations

from typing import Any

from draftly.content.models import ContentChannel, ContentVariant

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
    if variant.channel is ContentChannel.X and len(variant.body) > 280:
        issues.append("x variant exceeds 280 characters")
    for key, threshold in THRESHOLDS.items():
        if scores[key] < threshold:
            issues.append(f"{key} below threshold {threshold:.2f}")
    return {"passed": not issues, "scores": scores, "issues": issues}


def evaluate_content(variants: list[ContentVariant]) -> dict[str, Any]:
    results = [evaluate_content_variant(variant) for variant in variants]
    return {
        "passed": bool(results) and all(result["passed"] for result in results),
        "variants": results,
        "issues": [issue for result in results for issue in result["issues"]],
    }
