"""Evaluation service (plan §8.2) — orchestration facade."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from strands_evals import Case

from draftly.evaluation.failure_analyzer import FailureAnalyzer
from draftly.evaluation.runner import StrandsEvalsRunner

logger = logging.getLogger(__name__)


class EvaluationService:
    """Run golden-dataset evaluations and persist categorized results."""

    def __init__(
        self,
        repository: Any = None,
        org_id: str = "",
        analyzer: FailureAnalyzer | None = None,
    ) -> None:
        self.runner = StrandsEvalsRunner(
            repository=repository,
            org_id=org_id,
        )
        self.analyzer = analyzer or FailureAnalyzer()

    async def run_suite(
        self,
        *,
        dataset: str,
        evaluators: list[Any],
        get_response: Callable[[Case], Any],
    ) -> dict[str, Any]:
        """Load a named dataset, run it, analyze + persist the report."""
        cases = self.runner.load_dataset(dataset)
        report, record = await self.runner.run_and_persist(
            cases, evaluators, get_response, target_id=dataset
        )

        passes = list(getattr(report, "test_passes", []) or [])
        reasons = list(getattr(report, "reasons", []) or [])
        failures = [
            {"index": i, "reason": reasons[i] if i < len(reasons) else ""}
            for i, passed in enumerate(passes)
            if not passed
        ]
        analysis = self.analyzer.analyze(failures)

        return {
            "dataset": dataset,
            "overall_score": float(
                getattr(report, "overall_score", 0.0) or 0.0
            ),
            "cases": len(passes),
            "passed": sum(1 for p in passes if p),
            "failed": sum(1 for p in passes if not p),
            "failure_categories": analysis.categories,
            "dominant_failure": analysis.dominant_category(),
            "record": record,
        }
