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

    fake_cases = [
        type("Case", (), {"name": "c1", "input": "in", "expected_output": "out", "metadata": {}})(),
    ]

    class FakeRunner:
        def load_all_datasets(self):
            calls["called"] = True
            return {"geometry": fake_cases}

    monkeypatch.setattr(
        "draftly.persistence.repositories.evaluations.StrandsEvalsRunner", FakeRunner
    )

    datasets = repo.list_datasets()

    assert calls["called"] is True
    assert datasets == [
        {"name": "geometry", "cases": [
            {"name": "c1", "input": "in", "expected_output": "out", "metadata": {"name": "c1"}}
        ]}
    ]


def test_list_datasets_returns_empty_when_no_datasets(monkeypatch):
    repo = EvaluationRepository(store=FakeStore())

    class FakeRunner:
        def load_all_datasets(self):
            return {}

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
