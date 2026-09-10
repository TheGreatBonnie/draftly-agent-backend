from __future__ import annotations

from draftly.persistence.repositories.evaluations import EvaluationRepository


class DetailStore:
    def __init__(self) -> None:
        self.case_results: list[dict] = []
        self.summary_args: dict | None = None
        self.list_args: dict | None = None

    async def insert_case_results(self, **kwargs):
        self.case_results = kwargs["results"]
        return self.case_results

    async def get_by_run_id(self, **kwargs):
        self.summary_args = kwargs
        return {"id": "summary-1", "run_id": kwargs["run_id"]}

    async def list_case_results(self, **kwargs):
        self.list_args = kwargs
        return ([{"case_id": "case-1"}], "next-cursor")


async def test_save_case_results_forwards_run_scope_and_rows() -> None:
    store = DetailStore()
    repo = EvaluationRepository(store=store)

    results = [
        {
            "dataset": "documentation",
            "case_id": "oauth-auth",
            "metric": "expected_tools",
            "score": 100,
            "passed": True,
            "reason": "matched",
            "evidence": [{"tool": "search"}],
        }
    ]

    saved = await repo.save_case_results(
        org_id="org-1",
        evaluation_id="evaluation-1",
        run_id="run-1",
        results=results,
    )

    assert saved == results
    assert store.case_results == results


async def test_get_run_summary_is_scoped_to_organization_and_run() -> None:
    store = DetailStore()
    repo = EvaluationRepository(store=store)

    result = await repo.get_run_summary(org_id="org-1", run_id="run-1")

    assert result == {"id": "summary-1", "run_id": "run-1"}
    assert store.summary_args == {"org_id": "org-1", "run_id": "run-1"}


async def test_list_case_results_returns_rows_and_cursor() -> None:
    store = DetailStore()
    repo = EvaluationRepository(store=store)

    rows, next_cursor = await repo.list_case_results(
        org_id="org-1",
        run_id="run-1",
        cursor="cursor-1",
        limit=25,
    )

    assert rows == [{"case_id": "case-1"}]
    assert next_cursor == "next-cursor"
    assert store.list_args == {
        "org_id": "org-1",
        "run_id": "run-1",
        "cursor": "cursor-1",
        "limit": 25,
    }


def test_case_results_migration_is_additive_and_indexed() -> None:
    migration = (
        __file__.replace(
            "/tests/evaluation/test_evaluation_detail_persistence.py",
            "/src/draftly/persistence/migrations/055_evaluation_case_results.sql",
        )
    )
    sql = open(migration, encoding="utf-8").read().lower()

    assert "create table if not exists evaluation_case_results" in sql
    assert "unique (evaluation_id, dataset, case_id, metric)" in sql
    assert "create index" in sql
    assert "drop table" not in sql
