from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepeval import evaluate as deepeval_evaluate
from deepeval.dataset import EvaluationDataset
from deepeval.metrics import BaseMetric


@dataclass
class EvaluationRequest:
    name: str
    input: str
    actual_output: str
    expected_output: str | None = None
    metadata: dict[str, Any] | None = None


class DeepEvalClient:
    """Integration boundary for DeepEval.

    Wraps deepeval.evaluate() so the rest of Draftly does not
    depend on DeepEval internals.
    """

    def evaluate(
        self,
        *,
        dataset: EvaluationDataset,
        metrics: list[BaseMetric],
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results = deepeval_evaluate(
            dataset=dataset,
            metrics=metrics,
        )
        return [
            {
                "name": getattr(r, "metric", "unknown"),
                "score": getattr(r, "score", 0.0),
                "threshold": getattr(r, "threshold", 0.0),
                "passed": getattr(r, "success", False),
                "reason": getattr(r, "reason", ""),
            }
            for r in (results if isinstance(results, list) else [])
        ]

    def get_failure_analysis(
        self,
        evaluation_id: str,
    ) -> dict[str, Any]:
        """Retrieve structured failure analysis for a past evaluation."""
        return {
            "evaluation_id": evaluation_id,
            "failures": [],
            "status": "unknown",
        }
