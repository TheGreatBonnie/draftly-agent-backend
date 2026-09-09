from __future__ import annotations

from typing import Any

from draftly.persistence.repositories.github import (
    get_github_workflow_by_issue,
    list_github_workflows_record,
    save_github_workflow,
)


class FakeClient:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.row: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((query, args))
        return self.row

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        return self.rows


class _FetchAllClient(FakeClient):
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        super().__init__()
        self.responses = responses
        self.queries: list[str] = []

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        self.executed.append((query, args))
        return self.responses.pop(0)


async def test_save_github_workflow_writes_meta_columns() -> None:
    client = FakeClient()
    client.row = {"id": "wf-1"}

    await save_github_workflow(
        org_id="o-1",
        workflow_id="ev-1",          # = run_id
        run_id="ev-1",               # NEW
        installation_id=123,
        owner="acme",
        repo="api",
        issue_number=482,
        title="OAuth Documentation Update",  # NEW
        actor="sarah.chen",                  # NEW
        event_type="pull_request.merged",
        db=client,
    )

    sql, params = client.executed[0]
    assert "INSERT INTO github_workflows" in sql
    assert "run_id" in sql
    assert "title" in sql
    assert "actor" in sql
    assert "event_type" in sql
    # Check that the title and actor are in the params
    assert "OAuth Documentation Update" in params
    assert "sarah.chen" in params
    assert "pull_request.merged" in params


async def test_get_github_workflow_by_issue_returns_meta() -> None:
    client = FakeClient()
    client.row = {
        "id": "wf-1", "workflow_id": "ev-1", "run_id": "ev-1",
        "installation_id": 123, "owner": "acme", "repo": "api",
        "issue_number": 482, "status": "pending",
        "title": "OAuth Documentation Update", "actor": "sarah.chen",
    }
    row = await get_github_workflow_by_issue(owner="acme", repo="api", issue_number=482, db=client)
    assert row is not None
    assert row["title"] == "OAuth Documentation Update"
    assert row["actor"] == "sarah.chen"
    assert row["run_id"] == "ev-1"


async def test_list_github_workflows_record_shape() -> None:
    client = FakeClient()
    client.rows = [
        {
            "workflow_id": "ev-1", "run_id": "ev-1", "title": "Test PR",
            "owner": "acme", "repo": "api", "issue_number": 482,
            "actor": "sarah.chen", "event_type": "pull_request.merged",
            "status": "pending", "created_at": "2024-01-01T00:00:00Z"
        },
    ]
    # Need to also stub jobs and workflow_events fetches
    # Simulate the four-query pattern: github_workflows, jobs, light workflow_events,
    # then payload-only workflow_events for status-bearing event types.
    seq_events = [
        {"run_id": "ev-1", "seq": 1, "type": "workflow_result", "node_id": None},
        {"run_id": "ev-1", "seq": 2, "type": "node_start", "node_id": "classify"},
        {"run_id": "ev-1", "seq": 3, "type": "node_start", "node_id": "context"},
        {"run_id": "ev-1", "seq": 4, "type": "node_start", "node_id": "research"},
        {"run_id": "ev-1", "seq": 5, "type": "node_start", "node_id": "impact"},
        {"run_id": "ev-1", "seq": 6, "type": "node_start", "node_id": "update"},
        {"run_id": "ev-1", "seq": 7, "type": "node_start", "node_id": "evaluate"},
        {"run_id": "ev-1", "seq": 8, "type": "node_start", "node_id": "deliver"},
        {"run_id": "ev-1", "seq": 9, "type": "node_stop", "node_id": "classify"},
        {"run_id": "ev-1", "seq": 10, "type": "node_stop", "node_id": "context"},
        {"run_id": "ev-1", "seq": 11, "type": "node_stop", "node_id": "research"},
        {"run_id": "ev-1", "seq": 12, "type": "node_stop", "node_id": "impact"},
        {"run_id": "ev-1", "seq": 13, "type": "node_stop", "node_id": "update"},
        {"run_id": "ev-1", "seq": 14, "type": "node_stop", "node_id": "evaluate"},
        {"run_id": "ev-1", "seq": 15, "type": "node_stop", "node_id": "deliver"},
    ]
    payload_events = [
        {"run_id": "ev-1", "seq": 1, "type": "workflow_result",
         "payload": {"status": "COMPLETED"}},
    ] + [
        {"run_id": "ev-1", "seq": 9 + i, "type": "node_stop",
         "payload": {"status": "COMPLETED"}}
        for i in range(7)
    ]

    call_count = 0
    async def fetch_all(query: str, *args: Any) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        client.executed.append((query, args))
        if call_count == 1:
            return client.rows
        elif call_count == 2:
            # jobs
            return [
                {
                    "run_id": "ev-1",
                    "status": "completed",
                    "started_at": "2024-01-01T00:00:00Z",
                    "completed_at": "2024-01-01T00:01:00Z",
                }
            ]
        elif call_count == 3:
            # light workflow_events listing (no payload column)
            return seq_events
        else:
            # payload-only workflow_events for status-bearing types
            return payload_events

    client.fetch_all = fetch_all  # type: ignore

    # We need to mock list_github_workflows_record to use our fetch_all
    # Since list_github_workflows_record creates its own db calls, let's just test it directly
    # by patching the db instance
    client.fetch = fetch_all
    rows = await list_github_workflows_record(org_id="o-1", db=client)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "ev-1"
    assert rows[0]["title"] == "Test PR"
    assert rows[0]["repository"] == "acme/api"
    assert rows[0]["created_at"] == "2024-01-01T00:00:00Z"
    assert rows[0]["trigger_label"] == "PR #482"
    assert rows[0]["status"] == "completed"
    assert rows[0]["current_stage"] == "deliver"
    assert "stages" in rows[0]
    assert isinstance(rows[0]["stages"], list)
    assert rows[0]["time"] != "—"
    assert "run_id = ANY($1::TEXT[])" in client.executed[1][0]
    assert client.executed[1][1] == (["ev-1"],)


async def test_list_workflows_fetches_payload_only_for_status_events() -> None:
    client = _FetchAllClient([
        [
            {
                "workflow_id": "ev-1", "run_id": "ev-1", "title": "Test PR",
                "owner": "acme", "repo": "api", "issue_number": 482,
                "actor": "sarah.chen", "event_type": "pull_request.merged",
                "status": "pending", "created_at": "2024-01-01T00:00:00Z",
            },
        ],
        [
            {
                "run_id": "ev-1", "status": "completed",
                "started_at": "2024-01-01T00:00:00Z",
                "completed_at": "2024-01-01T00:01:00Z",
            },
        ],
        [
            {"run_id": "ev-1", "seq": 1, "type": "node_start", "node_id": "classify"},
            {"run_id": "ev-1", "seq": 2, "type": "node_start", "node_id": "context"},
            {"run_id": "ev-1", "seq": 3, "type": "node_stop", "node_id": "classify"},
            {"run_id": "ev-1", "seq": 4, "type": "node_stop", "node_id": "context"},
            {"run_id": "ev-1", "seq": 5, "type": "workflow_result"},
        ],
        [
            {"run_id": "ev-1", "seq": 3, "type": "node_stop",
             "payload": {"status": "COMPLETED"}},
            {"run_id": "ev-1", "seq": 4, "type": "node_stop",
             "payload": {"status": "COMPLETED"}},
            {"run_id": "ev-1", "seq": 5, "type": "workflow_result",
             "payload": {"status": "COMPLETED"}},
        ],
    ])

    rows = await list_github_workflows_record(org_id="o-1", db=client)

    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["current_stage"] == "context"
    assert rows[0]["stages"][0] == "done"
    assert rows[0]["stages"][1] == "done"
    assert rows[0]["stages"][2] == "waiting"

    listing_query, payload_query = client.queries[2], client.queries[3]
    assert "payload" not in listing_query
    assert "type IN ('workflow_result', 'node_stop')" in payload_query


async def test_list_workflows_bounds_github_query_and_run_ids() -> None:
    client = _FetchAllClient([[], [], [], []])
    rows = await list_github_workflows_record(org_id="o-1", db=client, limit=50)
    assert rows == []
    assert "LIMIT $2" in client.queries[0]
    assert client.executed[0][1] == ("o-1", 50)
