from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvaluationResult:
    passed: bool
    score: float
    failures: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class DeepEvalResults:
    @staticmethod
    def from_raw(
        *,
        score: float,
        threshold: float,
        failures: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        return EvaluationResult(
            passed=score >= threshold,
            score=score,
            failures=failures or [],
            metadata=metadata or {},
        )

    @staticmethod
    def from_deepeval_results(
        results: list[dict[str, Any]],
    ) -> list[EvaluationResult]:
        return [
            EvaluationResult(
                passed=r.get("passed", False),
                score=r.get("score", 0.0),
                failures=[r.get("reason", "")] if not r.get("passed") else [],
                metadata={"name": r.get("name", "unknown")},
            )
            for r in results
        ]
