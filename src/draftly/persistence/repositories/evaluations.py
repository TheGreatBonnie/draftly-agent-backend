from __future__ import annotations

from datetime import datetime
from typing import Any

from draftly.evaluation.runner import StrandsEvalsRunner
from draftly.integrations.database.evaluations_store import (
    DatabaseEvaluationsStore,
)


class EvaluationRepository:
    def __init__(
        self,
        store: DatabaseEvaluationsStore | None = None,
    ) -> None:
        self.store = store or DatabaseEvaluationsStore()

    async def create(
        self,
        *,
        org_id: str,
        evaluation_type: str,
        run_id: str | None = None,
        target_id: str | None,
        score: float,
        status: str,
        metrics: dict[str, Any],
        failures: list[dict[str, Any]],
        started_at: datetime,
        completed_at: datetime,
    ) -> dict[str, Any]:
        return await self.store.insert(
            org_id=org_id,
            evaluation_type=evaluation_type,
            run_id=run_id,
            target_id=target_id,
            score=score,
            status=status,
            metrics=metrics,
            failures=failures,
            started_at=started_at,
            completed_at=completed_at,
        )

    def list_datasets(self) -> list[dict[str, Any]]:
        """Golden datasets as serialized dicts for the evaluation graph."""
        loaded = StrandsEvalsRunner().load_all_datasets()
        return [
            {
                "name": name,
                "cases": [
                    {
                        "name": case.name,
                        "input": case.input,
                        "expected_output": getattr(case, "expected_output", None),
                        "metadata": {
                            **(case.metadata or {}),
                            "name": case.name,
                        }
                        if isinstance(case.metadata, dict)
                        else {"name": case.name},
                    }
                    for case in cases
                ],
            }
            for name, cases in loaded.items()
        ]

    async def save_run_summary(
        self,
        *,
        summary: dict[str, Any],
        org_id: str,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> dict[str, Any] | None:
        """Persist a run summary to the evaluations table."""
        total = int(summary.get("total") or 0)
        passed = int(summary.get("passed") or 0)
        failed = int(summary.get("failed") or 0)
        passed_all = bool(summary.get("passed_all") and total > 0)
        errors = list(summary.get("errors") or [])

        score = round((passed / total) * 100.0, 2) if total > 0 else 0.0
        status = "passed" if passed_all else "failed"
        failures = [
            {
                "case": str(error).split(":", maxsplit=1)[0],
                "reason": str(error).split(":", maxsplit=1)[-1].strip(),
                "index": index,
            }
            for index, error in enumerate(errors)
        ]
        metrics = {"cases": total, "passed": passed, "failed": failed}

        try:
            return await self.create(
                org_id=org_id,
                evaluation_type="documentation",
                run_id=run_id,
                target_id=run_id,
                score=score,
                status=status,
                metrics=metrics,
                failures=failures,
                started_at=started_at,
                completed_at=completed_at,
            )
        except Exception:
            from structlog import get_logger

            get_logger(__name__).exception("evaluation_save_summary_failed")
            return None

    async def get(
        self,
        *,
        evaluation_id: str,
    ) -> dict[str, Any] | None:
        return await self.store.get(evaluation_id=evaluation_id)

    async def search(
        self,
        *,
        org_id: str,
        evaluation_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self.store.search(
            org_id=org_id,
            evaluation_type=evaluation_type,
            limit=limit,
        )
