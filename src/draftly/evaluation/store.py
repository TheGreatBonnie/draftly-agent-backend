"""Database-backed EvaluationDataStore (plan §8.10).

Implements the strands evals ``EvaluationDataStore`` protocol (sync
``load``/``save``). The protocol methods are synchronous, so they only
maintain an in-memory cache; durable persistence to the
``012_evaluations`` tables happens in ``StrandsEvalsRunner`` (async)
after the experiment completes.
"""

from __future__ import annotations

from strands_evals.types import EvaluationData


class DatabaseEvaluationDataStore:
    """Case-result cache satisfying the EvaluationDataStore protocol."""

    def __init__(self) -> None:
        self._cache: dict[str, EvaluationData] = {}

    def load(self, case_name: str) -> EvaluationData | None:
        return self._cache.get(case_name)

    def save(self, case_name: str, result: EvaluationData) -> None:
        self._cache[case_name] = result

    def all_results(self) -> dict[str, EvaluationData]:
        return dict(self._cache)
