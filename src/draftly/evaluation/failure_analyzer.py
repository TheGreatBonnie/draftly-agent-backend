"""Failure analysis for evaluation runs (plan §8.2).

Categorizes failed cases (``test_pass=False`` + ``reason``) into
actionable buckets so the feedback loop can prioritize fixes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "grounding": ("evidence", "citation", "source", "grounded", "unsupported"),
    "completeness": ("incomplete", "missing", "omits", "omitted", "partial"),
    "correctness": ("incorrect", "wrong", "inaccurate", "false", "error"),
    "relevance": ("irrelevant", "off-topic", "unrelated", "not address"),
    "tone": ("tone", "professional", "clarity", "unclear"),
}


@dataclass
class FailureAnalysis:
    """Aggregated failure categories for one evaluation run."""

    total_failures: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)

    def dominant_category(self) -> str | None:
        if not self.categories:
            return None
        return max(self.categories.items(), key=lambda kv: kv[1])[0]


class FailureAnalyzer:
    """Turn a list of failure dicts into categorized insights."""

    def categorize(self, reason: str) -> str:
        lowered = (reason or "").lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return category
        return "other"

    def analyze(self, failures: list[dict[str, Any]]) -> FailureAnalysis:
        analysis = FailureAnalysis(total_failures=len(failures))
        for failure in failures:
            reason = str(failure.get("reason", ""))
            category = self.categorize(reason)
            analysis.categories[category] = analysis.categories.get(category, 0) + 1
            analysis.items.append(
                {
                    "case": failure.get("case") or failure.get("index"),
                    "category": category,
                    "reason": reason,
                    "score": failure.get("score"),
                }
            )
        logger.debug(
            "failure_analyzer_analyze",
            total_failures=analysis.total_failures,
            categories=analysis.categories,
            dominant=analysis.dominant_category(),
        )
        return analysis
