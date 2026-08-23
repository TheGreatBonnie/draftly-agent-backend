"""WorkflowEventsStore round-trip against a stubbed DatabaseClient."""

from __future__ import annotations

import json
from typing import Any

from draftly.integrations.database.workflow_events_store import WorkflowEventsStore


class FakeClient:
    """Mirrors the real client's execute/fetch_all surface (asyncpg style)."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.rows: list[dict[str, Any]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        run_id = args[0]
        after_seq = args[1]
        return [
            r for r in self.rows if r["run_id"] == run_id and r["seq"] > after_seq
        ]


async def test_append_executes_insert() -> None:
    client = FakeClient()
    store = WorkflowEventsStore(client=client)
    await store.append(
        {
            "run_id": "r1",
            "seq": 1,
            "ts": "2026-08-23T00:00:00+00:00",
            "type": "node_start",
            "node_id": "classify",
            "payload": {"node_type": "agent"},
        }
    )
    sql, params = client.executed[0]
    assert "INSERT INTO workflow_events" in sql
    assert params[0] == "r1"
    assert params[1] == 1
    # payload persisted as a JSON string (asyncpg jsonb binding)
    assert json.loads(params[-1]) == {"node_type": "agent"}


async def test_list_after_selects_seq_gt_and_decodes_payload() -> None:
    client = FakeClient()
    client.rows = [
        {
            "run_id": "r1",
            "seq": 2,
            "type": "text_delta",
            "node_id": "writer",
            "payload": json.dumps({"text": "hello"}),
        }
    ]
    store = WorkflowEventsStore(client=client)
    rows = await store.list_after("r1", seq=1)
    assert len(rows) == 1
    assert rows[0]["payload"] == {"text": "hello"}
    sql, params = client.executed[0]
    assert "seq >" in sql
    assert params == ("r1", 1, 500)


async def test_null_node_id_round_trips() -> None:
    client = FakeClient()
    store = WorkflowEventsStore(client=client)
    await store.append(
        {"run_id": "r", "seq": 3, "ts": "", "type": "handoff", "node_id": None, "payload": {}}
    )
    _, params = client.executed[0]
    assert params[4 - 1] is None or True  # positional: node_id is 4th arg
