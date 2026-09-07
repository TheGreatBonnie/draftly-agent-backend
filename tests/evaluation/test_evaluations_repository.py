from __future__ import annotations

from datetime import UTC, datetime

from draftly.persistence.repositories.evaluations import EvaluationRepository


class FakeStore:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    async def insert(self, **kwargs):
        row = {"id": "ev-1", **kwargs}
        self.inserted.append(row)
        return row

    async def get(self, *, evaluation_id: str):
        return None

    async def search(self, *, org_id, evaluation_type, limit):
        return []


def test_list_datasets_returns_serialized_cases(monkeypatch):
    repo = EvaluationRepository(store=FakeStore())
    calls = {"called": False}

    class FakeRunner:
        def load_all_dataset_definitions(self):
            calls["called"] = True
            return [
                {
                    "name": "geometry",
                    "description": "Geometry questions",
                    "surface": "support",
                    "required_tools": {"research": ["code_search"]},
                    "cases": [
                        {
                            "name": "c1",
                            "input": "in",
                            "expected_output": "out",
                            "metadata": {},
                        }
                    ],
                }
            ]

    monkeypatch.setattr(
        "draftly.persistence.repositories.evaluations.StrandsEvalsRunner", FakeRunner
    )

    datasets = repo.list_datasets()

    assert calls["called"] is True
    assert datasets == [
        {
            "name": "geometry",
            "description": "Geometry questions",
            "surface": "support",
            "required_tools": {"research": ["code_search"]},
            "cases": [
                {
                    "name": "c1",
                    "input": "in",
                    "expected_output": "out",
                    "metadata": {"name": "c1"},
                }
            ],
        }
    ]


def test_list_datasets_returns_empty_when_no_datasets(monkeypatch):
    repo = EvaluationRepository(store=FakeStore())

    class FakeRunner:
        def load_all_dataset_definitions(self):
            return []

    monkeypatch.setattr(
        "draftly.persistence.repositories.evaluations.StrandsEvalsRunner", FakeRunner
    )
    assert repo.list_datasets() == []


async def test_save_run_summary_inserts_via_store():
    store = FakeStore()
    repo = EvaluationRepository(store=store)

    started = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    completed = datetime(2026, 9, 2, 9, 1, tzinfo=UTC)
    record = await repo.save_run_summary(
        summary={
            "total": 20,
            "passed": 18,
            "failed": 2,
            "passed_all": False,
            "errors": ["dataset-a: boom"],
        },
        org_id="org-9",
        run_id="run-1",
        started_at=started,
        completed_at=completed,
    )

    assert record is not None
    inserted = store.inserted[0]
    assert inserted["org_id"] == "org-9"
    assert inserted["run_id"] == "run-1"
    assert inserted["evaluation_type"] == "documentation"
    assert inserted["score"] == 90.0
    assert inserted["status"] == "failed"
    assert inserted["metrics"] == {"cases": 20, "passed": 18, "failed": 2, "granular": []}
    assert inserted["failures"] == [{"case": "dataset-a", "reason": "boom", "index": 0}]
    assert inserted["started_at"] == started
    assert inserted["completed_at"] == completed


async def test_save_run_summary_persists_granular_rows():
    store = FakeStore()
    repo = EvaluationRepository(store=store)

    started = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    completed = datetime(2026, 9, 2, 9, 1, tzinfo=UTC)
    record = await repo.save_run_summary(
        summary={
            "total": 2,
            "passed": 1,
            "failed": 1,
            "passed_all": False,
            "errors": [],
            "rows": [
                {
                    "dataset": "documentation",
                    "case": "oauth-auth",
                    "metric": "expected_tools",
                    "score": 1.0,
                    "test_pass": True,
                    "reason": "tools matched",
                },
                {
                    "dataset": "documentation",
                    "case": "oauth-auth",
                    "metric": "expected_contains",
                    "score": 0.0,
                    "test_pass": False,
                    "reason": "not found",
                },
            ],
        },
        org_id="org-9",
        run_id="run-1",
        started_at=started,
        completed_at=completed,
    )

    assert record is not None
    inserted = store.inserted[0]
    assert inserted["score"] == 50.0
    assert inserted["metrics"]["cases"] == 2
    assert inserted["metrics"]["granular"] == [
        {
            "dataset": "documentation",
            "case": "oauth-auth",
            "metric": "expected_tools",
            "threshold": None,
            "score": 1.0,
            "test_pass": True,
            "reason": "tools matched",
        },
        {
            "dataset": "documentation",
            "case": "oauth-auth",
            "metric": "expected_contains",
            "threshold": None,
            "score": 0.0,
            "test_pass": False,
            "reason": "not found",
        },
    ]


async def test_save_run_summary_all_passed_is_passed_status():
    store = FakeStore()
    repo = EvaluationRepository(store=store)

    await repo.save_run_summary(
        summary={
            "total": 10,
            "passed": 10,
            "failed": 0,
            "passed_all": True,
            "errors": [],
        },
        org_id="org-9",
        run_id="run-1",
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
    )

    inserted = store.inserted[0]
    assert inserted["status"] == "passed"
    assert inserted["score"] == 100.0


async def test_save_run_summary_empty_run_failed_and_zero_score():
    store = FakeStore()
    repo = EvaluationRepository(store=store)

    await repo.save_run_summary(
        summary={"total": 0, "passed": 0, "failed": 0, "passed_all": False, "errors": []},
        org_id="org-9",
        run_id="run-1",
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
    )

    inserted = store.inserted[0]
    assert inserted["status"] == "failed"
    assert inserted["score"] == 0.0


async def test_save_run_summary_populates_passed_target_type_and_trace_id():
    store = FakeStore()
    repo = EvaluationRepository(store=store)

    started = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    completed = datetime(2026, 9, 2, 9, 1, tzinfo=UTC)
    await repo.save_run_summary(
        summary={"total": 2, "passed": 2, "failed": 0, "passed_all": True, "errors": []},
        org_id="org-9",
        run_id="run-1",
        started_at=started,
        completed_at=completed,
        evaluation_type="support",
    )

    inserted = store.inserted[0]
    assert inserted["passed"] is True
    assert inserted["target_type"] == "support"
    assert inserted["trace_id"] == "run-1"


async def test_save_run_summary_writes_summary_row_only():
    store = FakeStore()
    repo = EvaluationRepository(store=store)

    started = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    completed = datetime(2026, 9, 2, 9, 1, tzinfo=UTC)
    await repo.save_run_summary(
        summary={
            "total": 2,
            "passed": 1,
            "failed": 1,
            "passed_all": False,
            "errors": [],
            "rows": [
                {
                    "dataset": "support",
                    "case": "q1",
                    "metric": "expected_contains",
                    "score": 0.9,
                    "test_pass": True,
                    "reason": "covered",
                    "threshold": 0.45,
                },
                {
                    "dataset": "support",
                    "case": "q2",
                    "metric": "expected_contains",
                    "score": 0.2,
                    "test_pass": False,
                    "reason": "low coverage",
                    "threshold": 0.6,
                },
            ],
        },
        org_id="org-9",
        run_id="run-1",
        started_at=started,
        completed_at=completed,
        evaluation_type="support",
    )

    # Only the run-level summary row is stored; the per-(case, metric) detail
    # is embedded in the summary's metrics.granular instead of fanning out into
    # one row per result.
    assert len(store.inserted) == 1
    row = store.inserted[0]
    assert row["status"] == "failed"
    assert row["passed"] is False
    assert row["target_type"] == "support"
    assert row["trace_id"] == "run-1"
    assert row["metrics"]["cases"] == 2
    assert row["metrics"]["granular"] == [
        {
            "dataset": "support",
            "case": "q1",
            "metric": "expected_contains",
            "threshold": 0.45,
            "score": 0.9,
            "test_pass": True,
            "reason": "covered",
        },
        {
            "dataset": "support",
            "case": "q2",
            "metric": "expected_contains",
            "threshold": 0.6,
            "score": 0.2,
            "test_pass": False,
            "reason": "low coverage",
        },
    ]


async def test_save_run_summary_accepts_surface_evaluation_type():
    store = FakeStore()
    repo = EvaluationRepository(store=store)

    await repo.save_run_summary(
        summary={"total": 1, "passed": 1, "failed": 0, "passed_all": True, "errors": []},
        org_id="org-9",
        run_id="run-1",
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
        evaluation_type="github_issue",
    )

    inserted = store.inserted[0]
    assert inserted["evaluation_type"] == "github_issue"
