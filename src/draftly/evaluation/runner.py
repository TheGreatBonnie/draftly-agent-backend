"""Strands Evals runner (plan §8.2, §8.10).

Wraps ``Experiment(cases, evaluators)`` + ``run_evaluations_async`` and
persists the report to the evaluations repository (012_evaluations).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from strands_evals import Case, Experiment
from strands_evals.types.evaluation_report import EvaluationReport

logger = logging.getLogger(__name__)

DATASET_DIR = Path(__file__).parent / "datasets"


class StrandsEvalsRunner:
    """Run golden datasets through an Experiment and persist results."""

    def __init__(
        self,
        repository: Any = None,
        org_id: str = "",
        evaluation_type: str = "documentation",
    ) -> None:
        self.repository = repository
        self.org_id = org_id
        self.evaluation_type = evaluation_type

    # --------------------------------------------------------------
    # Dataset loading
    # --------------------------------------------------------------

    @staticmethod
    def load_dataset(name: str) -> list[Case]:
        """Load a golden dataset JSON file into Case objects."""
        path = DATASET_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"dataset not found: {path}")
        raw = json.loads(path.read_text())
        return [
            Case(
                name=case["name"],
                input=case["input"],
                expected_output=case.get("expected_output"),
                metadata=case.get("metadata") or {},
            )
            for case in raw.get("cases", [])
        ]

    def load_all_datasets(self) -> dict[str, list[Case]]:
        datasets: dict[str, list[Case]] = {}
        for path in sorted(DATASET_DIR.glob("*.json")):
            try:
                datasets[path.stem] = self.load_dataset(path.stem)
            except Exception:
                logger.exception("dataset_load_failed name=%s", path.stem)
        return datasets

    # --------------------------------------------------------------
    # Execution
    # --------------------------------------------------------------

    async def run(
        self,
        cases: list[Case],
        evaluators: list[Any],
        get_response: Callable[[Case], Any],
    ) -> EvaluationReport:
        """Run one experiment; returns the strands EvaluationReport."""
        experiment = Experiment(cases=cases, evaluators=evaluators)
        return await experiment.run_evaluations_async(get_response)

    async def run_and_persist(
        self,
        cases: list[Case],
        evaluators: list[Any],
        get_response: Callable[[Case], Any],
        *,
        target_id: str | None = None,
    ) -> tuple[EvaluationReport, dict[str, Any] | None]:
        """Run and persist a summary record; returns (report, record)."""
        report = await self.run(cases, evaluators, get_response)
        record = await self.persist_report(report, target_id=target_id)
        return report, record

    # --------------------------------------------------------------
    # Persistence
    # --------------------------------------------------------------

    async def persist_report(
        self,
        report: EvaluationReport,
        *,
        target_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Write the run summary + failures to the evaluations tables."""
        if self.repository is None:
            return None

        overall = float(getattr(report, "overall_score", 0.0) or 0.0)
        passes = list(getattr(report, "test_passes", []) or [])
        reasons = list(getattr(report, "reasons", []) or [])
        case_names = [
            str(case.get("name", index))
            if isinstance(case, dict)
            else str(getattr(case, "name", index))
            for index, case in enumerate(getattr(report, "cases", []) or [])
        ]

        failures = [
            {
                "case": case_names[index] if index < len(case_names) else index,
                "reason": reasons[index] if index < len(reasons) else "",
                "index": index,
            }
            for index, passed in enumerate(passes)
            if not passed
        ]

        metrics = {
            "cases": len(passes),
            "passed": sum(1 for p in passes if p),
            "failed": sum(1 for p in passes if not p),
        }

        started_at = datetime.now(UTC)
        try:
            return await self.repository.create(
                org_id=self.org_id,
                evaluation_type=self.evaluation_type,
                target_id=target_id,
                score=overall,
                status="passed" if all(passes) else "failed",
                metrics=metrics,
                failures=failures,
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )
        except Exception:
            logger.exception("evaluation_persist_failed")
            return None


def _summary(report: EvaluationReport) -> dict[str, Any]:
    return {
        "overall_score": float(getattr(report, "overall_score", 0.0) or 0.0),
        "cases": len(list(getattr(report, "test_passes", []) or [])),
    }


def run_dataset_sync(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic offline dataset runner for the CI evaluation graph.

    Each case: {"input": ..., "expected_output": ...}. A case passes when
    the expected output text appears in the stub response produced for the
    input — the "system under test" is the identity function over the
    expected output, so this validates dataset shape and runner plumbing.
    Sync on purpose: the graph executes it via ``asyncio.to_thread``
    because ``Experiment.run_evaluations`` calls ``asyncio.run``.
    """
    from strands_evals.evaluators import Contains

    cases = [
        Case(
            name=case.get("name", f"case-{index + 1}"),
            input=case.get("input", ""),
            expected_output=case.get("expected_output", ""),
        )
        for index, case in enumerate(dataset.get("cases", []))
    ]
    if not cases:
        return []

    experiment = Experiment(
        cases=cases,
        evaluators=[Contains(value=str(case.expected_output or "")) for case in cases],
    )
    report = experiment.run_evaluations(
        lambda case: str(getattr(case, "expected_output", "") or "")
    )
    return report_rows(dataset.get("name", "dataset"), report)


def report_rows(dataset_name: str, report: Any) -> list[dict[str, Any]]:
    """Flatten a report into per-evaluation row dicts."""
    rows: list[dict[str, Any]] = []
    # detailed_results: list[list[EvaluationOutput]] — one inner list per
    # case (one output per evaluator applied to that case).
    for case_outputs in getattr(report, "detailed_results", None) or []:
        for item in case_outputs:
            rows.append(
                {
                    "dataset": dataset_name,
                    "score": float(getattr(item, "score", 0.0) or 0.0),
                    "test_pass": bool(getattr(item, "test_pass", False)),
                    "reason": str(getattr(item, "reason", "")),
                }
            )
    return rows
