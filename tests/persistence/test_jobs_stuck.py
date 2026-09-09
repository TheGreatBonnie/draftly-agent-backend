"""DatabaseJobsStore.list_stuck — find long-running 'running' rows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from draftly.integrations.database.jobs_store import DatabaseJobsStore


class _FakeClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        return self.rows


async def test_list_stuck_queries_running_before_cutoff() -> None:
    client = _FakeClient(
        [{"run_id": "r-1", "org_id": "o-1", "started_at": "2026-01-01T00:00:00Z"}]
    )
    store = DatabaseJobsStore(client=client)

    rows = await store.list_stuck(
        started_before=datetime(2026, 1, 2, tzinfo=UTC),
        status="running",
        limit=50,
    )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "r-1"
    assert rows[0]["org_id"] == "o-1"
    sql, params = client.calls[0]
    assert "status = $2" in sql
    assert "started_at < $1" in sql
    assert "LIMIT $3" in sql
    assert params == (datetime(2026, 1, 2, tzinfo=UTC), "running", 50)


async def test_repo_list_stuck_passthrough() -> None:
    client = _FakeClient(
        [{"run_id": "r-2", "org_id": "o-1", "started_at": "2026-01-01T00:00:00Z"}]
    )
    from draftly.persistence.repositories.jobs import JobRepositoryImpl

    repo = JobRepositoryImpl(store=DatabaseJobsStore(client=client))

    rows = await repo.list_stuck(
        started_before=datetime(2026, 1, 2, tzinfo=UTC),
        status="running",
        limit=10,
    )

    assert rows[0]["run_id"] == "r-2"