from draftly.persistence.repositories.workflow_progress import project_progress
from draftly.persistence.repositories.workflows import WorkflowRunsRepository


def test_project_progress_uses_first_seen_node_order_and_latest_state():
    events = [
        {"seq": 2, "type": "node_start", "node_id": "research", "payload": {}},
        {"seq": 1, "type": "node_start", "node_id": "classify", "payload": {}},
        {"seq": 3, "type": "node_stop", "node_id": "classify", "payload": {"status": "COMPLETED"}},
        {"seq": 4, "type": "node_stop", "node_id": "research", "payload": {"status": "FAILED"}},
    ]
    assert project_progress(events) == {
        "current_stage": "research",
        "stage_states": {"classify": "completed", "research": "failed"},
        "stage_sequence": ["classify", "research"],
    }


def test_project_progress_ignores_non_node_events():
    assert project_progress([{"seq": 1, "type": "tool_progress", "node_id": "write"}]) == {
        "current_stage": None, "stage_states": {}, "stage_sequence": []
    }


async def test_run_page_projects_historical_progress_in_one_query():
    class Database:
        def __init__(self):
            self.calls = []

        async def fetch_all(self, query, *args):
            self.calls.append((query, args))
            return [
                {"run_id": "run-1", "seq": 1, "type": "node_start", "node_id": "classify",
                 "payload": {}},
                {"run_id": "run-1", "seq": 2, "type": "node_stop", "node_id": "classify",
                 "payload": {"status": "COMPLETED"}},
            ]

    db = Database()
    rows = await WorkflowRunsRepository(database=db).with_progress([
        {"id": "run-1", "stage_states": {}, "current_stage": None},
        {"id": "run-2", "stage_states": {}, "current_stage": None},
    ])
    assert len(db.calls) == 1
    assert db.calls[0][1] == (["run-1", "run-2"],)
    assert rows[0]["stage_sequence"] == ["classify"]
    assert rows[0]["stage_states"] == {"classify": "completed"}
    assert rows[1]["stage_sequence"] == []
