from __future__ import annotations

from deepeval.metrics import BaseMetric, GEval, StepEfficiencyMetric, TaskCompletionMetric


class DeepEvalMetrics:
    """Registry for DeepEval metric instances."""

    def __init__(self):
        self._metrics: dict[str, BaseMetric] = {}

    def register(
        self,
        name: str,
        metric: BaseMetric,
    ) -> BaseMetric:
        self._metrics[name] = metric
        return metric

    def get(self, name: str) -> BaseMetric:
        return self._metrics[name]

    def all(self) -> list[BaseMetric]:
        return list(self._metrics.values())

    @staticmethod
    def default_trace_metrics() -> list[BaseMetric]:
        return [
            TaskCompletionMetric(threshold=0.90),
            StepEfficiencyMetric(threshold=0.80),
        ]

    @staticmethod
    def default_doc_quality_metrics() -> list[GEval]:
        from deepeval.test_case import SingleTurnParams

        return [
            GEval(
                name="Documentation Accuracy",
                criteria=(
                    "Does the generated documentation accurately reflect "
                    "the current software implementation?"
                ),
                evaluation_params=[
                    SingleTurnParams.INPUT,
                    SingleTurnParams.ACTUAL_OUTPUT,
                ],
                threshold=0.90,
            ),
            GEval(
                name="Documentation Completeness",
                criteria="Does the documentation cover all user-facing changes?",
                evaluation_params=[
                    SingleTurnParams.INPUT,
                    SingleTurnParams.ACTUAL_OUTPUT,
                ],
                threshold=0.85,
            ),
        ]
