from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from draftly.orchestration.nodes.evaluate import compute_quality


def evaluate_document_content(
    content: str,
    *,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score one source or Draftly revision with the shared quality gate."""
    evidence_items = evidence or []
    score, reasons = compute_quality(evidence_items, content)
    lowered = content.lower()
    topics = [
        str(item.get("topic") or "").strip().lower()
        for item in evidence_items
        if str(item.get("topic") or "").strip()
    ]
    covered = sum(1 for topic in topics if topic in lowered)
    coverage = covered / max(len(evidence_items), 1)
    completeness = covered / max(len(topics), 1)
    length = min(len(content) / 500, 1.0)
    return {
        "score": round(score * 100, 2),
        "passed": score >= 0.6,
        "status": "passed" if score >= 0.6 else "failed",
        "metrics": {
            "evidence_coverage": round(coverage, 4),
            "completeness": round(completeness, 4),
            "length": round(length, 4),
        },
        "failures": [] if score >= 0.6 else [{"reason": "; ".join(reasons)}],
        "reasons": reasons,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
