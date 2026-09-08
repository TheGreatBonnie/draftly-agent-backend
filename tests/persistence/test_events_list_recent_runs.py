"""EventRepository.list_recent_runs — reconciliation sweep input."""

from __future__ import annotations

from draftly.persistence.repositories.events import EventRepository


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def fetch_all(self, query, *params):
        return self._rows


async def test_list_recent_runs_returns_event_id_and_status() -> None:
    repo = EventRepository(database=_FakeDB(
        [{"event_id": "a", "status": "completed"}, {"event_id": "b", "status": "running"}]
    ))

    rows = await repo.list_recent_runs(limit=5)

    assert rows == [
        {"event_id": "a", "status": "completed"},
        {"event_id": "b", "status": "running"},
    ]
